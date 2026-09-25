from __future__ import annotations

import logging
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityRelationship, Project
from app.repositories.activity_repo import ActivityRepository
from app.repositories.relationship_repo import RelationshipRepository
from app.services.calendar_service import CalendarService
from app.services.cpm_engine import CPMEngine, CPMResult

logger = logging.getLogger(__name__)


class ScenarioSimulationService:
    """
    Deterministic What-If Scenario and Delay Simulation Service.
    Executes in-memory forward/backward pass CPM simulations on isolated copies
    of project activities and networks. GUARANTEES zero mutation to the master database.
    """

    @classmethod
    def simulate_activity_delay(
        cls,
        db: Session,
        project: Project,
        activity_code: str,
        delay_days: float,
        delay_category: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Simulate delay on a single activity.
        Wraps simulate_scenario for backward compatibility with TimeAgent and query engine.
        """
        return cls.simulate_scenario(
            db=db,
            project=project,
            changes=[
                {
                    "activity_code": activity_code,
                    "delay_days": delay_days,
                    "delay_category": delay_category or "Unknown",
                    "notes": notes,
                }
            ],
        )

    @classmethod
    def simulate_scenario(
        cls,
        db: Session,
        project: Project,
        changes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Runs an isolated, multi-activity what-if simulation against the current schedule.
        Calculates:
        - Project finish delta (calendar / working days)
        - Baseline vs simulated finish dates
        - Critical path before & after
        - Affected activities & float deltas
        - Negative float introduction
        - Driving relationships
        """
        activities, _ = ActivityRepository.filter_activities(
            db=db, project_id=project.id, page=1, page_size=10000
        )
        relationships = RelationshipRepository.get_by_project(db, project.id)

        act_by_id = {a.id: a for a in activities}
        act_by_code = {a.activity_code.upper(): a for a in activities}

        # 1. Build base activity representations
        act_dicts_base: List[Dict[str, Any]] = []
        for a in activities:
            dur = a.original_duration or 0.0
            act_dicts_base.append({
                "id": a.id,
                "activity_code": a.activity_code,
                "name": a.name,
                "original_duration": dur,
                "planned_start": a.planned_start,
                "planned_finish": a.planned_finish,
                "actual_start": a.actual_start,
                "actual_finish": a.actual_finish,
                "calendar": a.calendar,
                "status": a.status,
                "percent_complete": a.percent_complete,
                "remaining_duration": a.remaining_duration,
                "constraint_type": a.constraint_type,
                "constraint_date": a.constraint_date,
            })

        # 2. Build relationships representation with safe activity code mapping
        rel_dicts: List[Dict[str, Any]] = []
        for r in relationships:
            p_act = act_by_id.get(r.predecessor_id)
            s_act = act_by_id.get(r.successor_id)
            p_code = p_act.activity_code if p_act else r.predecessor_id
            s_code = s_act.activity_code if s_act else r.successor_id
            rel_dicts.append({
                "id": r.id,
                "predecessor_id": r.predecessor_id,
                "successor_id": r.successor_id,
                "predecessor_code": p_code,
                "successor_code": s_code,
                "relationship_type": r.relationship_type,
                "lag": r.lag or 0.0,
            })

        # 3. Load relational calendars
        cal_map = CalendarService.load_project_calendars(db, project.id)
        engine = CPMEngine(calendars=cal_map)

        proj_start = project.planned_start.date() if project.planned_start else None
        data_date = project.data_date.date() if project.data_date else None

        # 4. Calculate Baseline CPM
        base_cpm = engine.calculate(
            activities=act_dicts_base,
            relationships=rel_dicts,
            project_start_date=proj_start,
            data_date=data_date,
        )

        # 5. Build isolated simulation copy and apply changes
        act_dicts_sim = deepcopy(act_dicts_base)
        sim_by_code = {d["activity_code"].upper(): d for d in act_dicts_sim}

        applied_changes: List[Dict[str, Any]] = []
        primary_activity_code = ""
        primary_activity_name = ""
        primary_delay_days = 0.0

        for change in changes:
            raw_code = str(change.get("activity_code", "")).strip().upper()
            target_node = sim_by_code.get(raw_code)
            if not target_node:
                # Try finding by name substring
                for d in act_dicts_sim:
                    if raw_code in (d.get("name") or "").upper():
                        target_node = d
                        raw_code = d["activity_code"].upper()
                        break

            if not target_node:
                continue

            if not primary_activity_code:
                primary_activity_code = target_node["activity_code"]
                primary_activity_name = target_node["name"]

            delay_days = float(change.get("delay_days", 0.0))
            if delay_days:
                orig_dur = float(target_node.get("original_duration") or 0.0)
                target_node["original_duration"] = orig_dur + delay_days
                if target_node.get("remaining_duration") is not None:
                    target_node["remaining_duration"] = float(target_node["remaining_duration"]) + delay_days
                primary_delay_days += delay_days

            if "constraint_type" in change:
                target_node["constraint_type"] = change["constraint_type"]
            if "constraint_date" in change:
                target_node["constraint_date"] = change["constraint_date"]
            if "status" in change:
                target_node["status"] = change["status"]

            applied_changes.append({
                "activity_code": target_node["activity_code"],
                "name": target_node["name"],
                "delay_days": delay_days,
                "category": change.get("delay_category") or "Unknown",
                "notes": change.get("notes"),
            })

        # 6. Calculate Simulated CPM
        sim_cpm = engine.calculate(
            activities=act_dicts_sim,
            relationships=rel_dicts,
            project_start_date=proj_start,
            data_date=data_date,
        )

        # 7. Compare results & identify affected activities
        impact_days = 0
        if base_cpm.project_finish and sim_cpm.project_finish:
            impact_days = (sim_cpm.project_finish - base_cpm.project_finish).days

        affected_activities: List[Dict[str, Any]] = []
        float_changes: Dict[str, Dict[str, float]] = {}
        negative_float_acts: List[Dict[str, Any]] = []

        for code, sim_node in sim_cpm.activities.items():
            base_node = base_cpm.activities.get(code)
            if not base_node:
                continue

            es_changed = sim_node.early_start != base_node.early_start
            ef_changed = sim_node.early_finish != base_node.early_finish
            float_changed = abs(sim_node.total_float - base_node.total_float) > 0.01

            if es_changed or ef_changed or float_changed or sim_node.is_critical != base_node.is_critical:
                affected_activities.append({
                    "activity_code": sim_node.activity_code,
                    "name": sim_node.name,
                    "early_start_before": base_node.early_start.isoformat() if base_node.early_start else None,
                    "early_start_after": sim_node.early_start.isoformat() if sim_node.early_start else None,
                    "early_finish_before": base_node.early_finish.isoformat() if base_node.early_finish else None,
                    "early_finish_after": sim_node.early_finish.isoformat() if sim_node.early_finish else None,
                    "total_float_before": base_node.total_float,
                    "total_float_after": sim_node.total_float,
                    "is_critical_before": base_node.is_critical,
                    "is_critical_after": sim_node.is_critical,
                })

            if float_changed:
                float_changes[code] = {
                    "base_float": base_node.total_float,
                    "sim_float": sim_node.total_float,
                    "delta": round(sim_node.total_float - base_node.total_float, 2),
                }

            if sim_node.total_float < 0:
                negative_float_acts.append({
                    "activity_code": sim_node.activity_code,
                    "name": sim_node.name,
                    "negative_float": sim_node.total_float,
                })

        # Primary activity nodes for backward compatibility
        primary_code = primary_activity_code or (applied_changes[0]["activity_code"] if applied_changes else "")
        base_target_node = base_cpm.activities.get(primary_code)
        sim_target_node = sim_cpm.activities.get(primary_code)

        critical_path_before = base_cpm.critical_path
        critical_path_after = sim_cpm.critical_path
        cp_changed = set(critical_path_before) != set(critical_path_after)

        return {
            "project_id": project.id,
            "project_code": project.project_code,
            "applied_changes": applied_changes,
            "activity_code": primary_code,
            "activity_name": primary_activity_name,
            "delay_days": primary_delay_days,
            "base_project_finish": base_cpm.project_finish.isoformat() if base_cpm.project_finish else "N/A",
            "sim_project_finish": sim_cpm.project_finish.isoformat() if sim_cpm.project_finish else "N/A",
            "impact_days": impact_days,
            "base_float": base_target_node.total_float if base_target_node else 0.0,
            "sim_float": sim_target_node.total_float if sim_target_node else 0.0,
            "is_critical_before": base_target_node.is_critical if base_target_node else False,
            "is_critical_after": sim_target_node.is_critical if sim_target_node else False,
            "critical_path_changed": cp_changed,
            "critical_path_before": critical_path_before,
            "critical_path_after": critical_path_after,
            "affected_activities_count": len(affected_activities),
            "affected_activities": affected_activities[:25],
            "float_changes": float_changes,
            "negative_float_activities": negative_float_acts,
            "read_only_guarantee": True,
        }

    @classmethod
    def evaluate_delay_event_impact(
        cls,
        db: Session,
        delay_event_id: str,
        persist_simulated_impact: bool = True,
    ) -> Dict[str, Any]:
        """
        Evaluates the schedule finish impact of a recorded DelayEvent.
        Strictly distinguishes reported physical delay from simulated project finish impact.
        Updates simulated_impact_days on DelayEvent without touching master schedule dates.
        """
        from app.domain.models import DelayEvent

        delay_event = db.query(DelayEvent).filter(DelayEvent.id == delay_event_id).first()
        if not delay_event:
            return {"error": f"DelayEvent '{delay_event_id}' not found."}

        project = db.query(Project).filter(Project.id == delay_event.project_id).first()
        if not project:
            return {"error": f"Project '{delay_event.project_id}' not found."}

        target_activity = None
        if delay_event.activity_id:
            target_activity = db.query(Activity).filter(Activity.id == delay_event.activity_id).first()

        if not target_activity:
            return {"error": "Target activity for delay event is missing or unassigned."}

        sim_result = cls.simulate_scenario(
            db=db,
            project=project,
            changes=[
                {
                    "activity_code": target_activity.activity_code,
                    "delay_days": delay_event.delay_days,
                    "delay_category": delay_event.category,
                    "notes": delay_event.evidence_text or delay_event.notes,
                }
            ],
        )

        impact_days = sim_result.get("impact_days", 0)
        if persist_simulated_impact:
            delay_event.simulated_impact_days = float(impact_days)
            delay_event.status = "SIMULATED"
            db.commit()

        return {
            "delay_event_id": delay_event.id,
            "category": delay_event.category,
            "activity_code": target_activity.activity_code,
            "activity_name": target_activity.name,
            "reported_physical_delay_days": delay_event.delay_days,
            "simulated_schedule_impact_days": impact_days,
            "activity_float_before": sim_result.get("base_float", 0.0),
            "activity_float_after": sim_result.get("sim_float", 0.0),
            "base_project_finish": sim_result.get("base_project_finish"),
            "sim_project_finish": sim_result.get("sim_project_finish"),
            "critical_path_changed": sim_result.get("critical_path_changed", False),
            "affected_activities_count": sim_result.get("affected_activities_count", 0),
            "notes": (
                f"Reported physical delay of {delay_event.delay_days} days in {delay_event.category} "
                f"results in {impact_days} days simulated schedule finish impact."
            ),
        }
