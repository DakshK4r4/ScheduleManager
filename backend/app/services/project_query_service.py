from __future__ import annotations

import json
import logging
import re
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Set

from sqlalchemy import func, or_, and_, desc, asc
from sqlalchemy.orm import Session, joinedload

from app.domain.models import (
    Activity,
    ActivityRelationship,
    Conversation,
    ConversationMessage,
    Project,
    WBSNode,
)
from app.repositories.activity_repo import ActivityRepository
from app.repositories.relationship_repo import RelationshipRepository
from app.services.cpm_engine import CPMEngine, CPMResult, CPMActivityNode
CPMNode = CPMActivityNode
from app.services.calendar_service import CalendarService
from app.services.credential_resolver import CredentialResolver
from app.services.sarvam_service import SarvamService

logger = logging.getLogger(__name__)


class ProjectQueryService:
    """
    General Project-Aware Query Engine for Time Agent.
    Executes real-time, read-only queries, calculations, CPM analyses,
    and aggregations against the authoritative project schedule in PostgreSQL.
    """

    # -------------------------------------------------------------------------
    # 1. CORE STRUCTURED TOOLS (All query the live database)
    # -------------------------------------------------------------------------

    @classmethod
    def get_cpm_analysis(cls, db: Session, project: Project) -> CPMResult:
        """Runs the CPM engine to compute early/late dates, float, and critical paths."""
        activities, _ = ActivityRepository.filter_activities(
            db=db, project_id=project.id, page=1, page_size=5000
        )
        relationships = RelationshipRepository.get_by_project(db, project.id)

        cal_map = CalendarService.load_project_calendars(db, project.id)
        act_dicts = [
            {
                "id": a.id,
                "activity_code": a.activity_code,
                "name": a.name,
                "status": a.status,
                "planned_start": a.planned_start,
                "planned_finish": a.planned_finish,
                "actual_start": a.actual_start,
                "actual_finish": a.actual_finish,
                "original_duration": a.original_duration or 0.0,
                "percent_complete": a.percent_complete,
                "remaining_duration": a.remaining_duration,
                "constraint_type": a.constraint_type,
                "constraint_date": a.constraint_date,
                "calendar_id": a.calendar,
            }
            for a in activities
        ]
        rel_dicts = [
            {
                "id": r.id,
                "predecessor_id": r.predecessor_id,
                "successor_id": r.successor_id,
                "predecessor_code": r.predecessor_code,
                "successor_code": r.successor_code,
                "relationship_type": r.relationship_type,
                "lag": r.lag,
            }
            for r in relationships
        ]
        engine = CPMEngine(calendars=cal_map)
        return engine.calculate(
            activities=act_dicts,
            relationships=rel_dicts,
            project_start_date=project.planned_start.date() if project.planned_start else None,
            data_date=project.data_date.date() if project.data_date else None,
        )

    @classmethod
    def get_project_summary(cls, db: Session, project: Project) -> Dict[str, Any]:
        """Provides a comprehensive statistical and milestone summary of the project."""
        activities = (
            db.query(Activity)
            .options(joinedload(Activity.wbs_node))
            .filter(Activity.project_id == project.id)
            .all()
        )
        wbs_count = db.query(func.count(WBSNode.id)).filter(WBSNode.project_id == project.id).scalar() or 0
        rel_count = db.query(func.count(ActivityRelationship.id)).filter(ActivityRelationship.project_id == project.id).scalar() or 0

        cpm_res = cls.get_cpm_analysis(db, project)

        total_acts = len(activities)
        completed = [a for a in activities if a.status == "COMPLETED" or (a.percent_complete or 0) >= 100.0]
        in_progress = [a for a in activities if a.status == "IN_PROGRESS" and (a.percent_complete or 0) < 100.0]
        not_started = [a for a in activities if a.status == "NOT_STARTED" and (a.percent_complete or 0) == 0.0]

        total_dur = sum((a.original_duration or 0.0) for a in activities)
        comp_dur = sum((a.original_duration or 0.0) * ((a.percent_complete or 0.0) / 100.0) for a in activities)
        progress_weighted = round((comp_dur / total_dur * 100.0), 1) if total_dur > 0 else 0.0
        progress_simple = round(sum(a.percent_complete or 0.0 for a in activities) / total_acts, 1) if total_acts > 0 else 0.0

        return {
            "project_code": project.project_code,
            "project_name": project.name,
            "data_date": project.data_date.strftime("%Y-%m-%d") if project.data_date else "Not Set",
            "planned_start": project.planned_start.strftime("%Y-%m-%d") if project.planned_start else "N/A",
            "planned_finish": project.planned_finish.strftime("%Y-%m-%d") if project.planned_finish else "N/A",
            "forecast_finish": cpm_res.project_finish.strftime("%Y-%m-%d") if cpm_res.project_finish else "N/A",
            "cpm_duration_days": cpm_res.project_duration_days,
            "activity_count": total_acts,
            "wbs_count": wbs_count,
            "relationship_count": rel_count,
            "completed_count": len(completed),
            "in_progress_count": len(in_progress),
            "not_started_count": len(not_started),
            "progress_weighted": progress_weighted,
            "progress_simple": progress_simple,
            "critical_activities_count": len([n for n in cpm_res.activities.values() if n.is_critical]),
            "negative_float_count": len(cpm_res.negative_float_activities),
        }

    @classmethod
    def get_wbs(cls, db: Session, project: Project) -> List[Dict[str, Any]]:
        """Returns all WBS nodes with activity counts."""
        nodes = db.query(WBSNode).filter(WBSNode.project_id == project.id).order_by(WBSNode.code).all()
        results = []
        for n in nodes:
            count = db.query(func.count(Activity.id)).filter(Activity.wbs_id == n.id).scalar() or 0
            results.append({
                "id": n.id,
                "code": n.code,
                "name": n.name,
                "parent_id": n.parent_id,
                "activity_count": count,
            })
        return results

    @classmethod
    def search_activities(
        cls,
        db: Session,
        project: Project,
        filters: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Filters and retrieves activities matching dynamic natural-language criteria.
        Returns serialized activity objects with CPM schedule metrics attached.
        """
        query = (
            db.query(Activity)
            .options(joinedload(Activity.wbs_node))
            .filter(Activity.project_id == project.id)
        )

        # 1. Scoped activity IDs (from conversation follow-up)
        scoped_ids = filters.get("activity_ids")
        if scoped_ids:
            query = query.filter(Activity.id.in_(scoped_ids))

        # 2. WBS or Discipline filter
        wbs_or_disc = filters.get("wbs_or_discipline")
        if wbs_or_disc:
            term = str(wbs_or_disc).strip().lower()
            query = query.outerjoin(WBSNode, Activity.wbs_id == WBSNode.id).filter(
                or_(
                    func.lower(WBSNode.name).contains(term),
                    func.lower(WBSNode.code).contains(term),
                    func.lower(Activity.discipline).contains(term),
                    func.lower(Activity.name).contains(term),
                )
            )

        # 3. Status filter
        status_req = filters.get("status")
        if status_req:
            st = str(status_req).upper()
            if st in ("COMPLETED", "COMPLETE"):
                query = query.filter(or_(Activity.status == "COMPLETED", Activity.percent_complete >= 100.0))
            elif st in ("IN_PROGRESS", "PROGRESS", "ACTIVE", "ONGOING"):
                query = query.filter(Activity.status == "IN_PROGRESS", Activity.percent_complete < 100.0)
            elif st in ("NOT_STARTED", "PENDING", "PLANNED", "INCOMPLETE"):
                if st == "INCOMPLETE":
                    query = query.filter(or_(Activity.status != "COMPLETED", Activity.percent_complete < 100.0))
                else:
                    query = query.filter(Activity.status == "NOT_STARTED", Activity.percent_complete == 0.0)

        # 4. Keyword search
        kw = filters.get("keyword")
        if kw:
            term = str(kw).strip().lower()
            query = query.filter(
                or_(
                    func.lower(Activity.activity_code).contains(term),
                    func.lower(Activity.name).contains(term),
                    func.lower(Activity.location_code).contains(term),
                    func.lower(Activity.discipline).contains(term),
                    func.lower(Activity.contractor_name).contains(term),
                )
            )

        # 5. Percentage bounds
        if filters.get("min_percent") is not None:
            query = query.filter(Activity.percent_complete >= float(filters["min_percent"]))
        if filters.get("max_percent") is not None:
            query = query.filter(Activity.percent_complete <= float(filters["max_percent"]))

        # 6. Sorting
        if filters.get("longest_duration"):
            query = query.order_by(desc(Activity.original_duration))
        elif filters.get("shortest_duration"):
            query = query.order_by(asc(Activity.original_duration))
        elif filters.get("least_float"):
            query = query.order_by(asc(Activity.total_float))
        elif filters.get("sort_by") == "planned_start":
            query = query.order_by(asc(Activity.planned_start))
        elif filters.get("sort_by") == "planned_finish":
            query = query.order_by(asc(Activity.planned_finish))
        else:
            query = query.order_by(asc(Activity.planned_start), asc(Activity.activity_code))

        activities = query.all()
        cpm_res = cls.get_cpm_analysis(db, project)

        results = []
        for a in activities:
            cpm_node = cpm_res.activities.get(a.id) or next(
                (n for n in cpm_res.activities.values() if n.activity_code == a.activity_code), None
            )

            p_start = a.planned_start.strftime("%Y-%m-%d") if a.planned_start else "N/A"
            p_finish = a.planned_finish.strftime("%Y-%m-%d") if a.planned_finish else "N/A"
            f_start = cpm_node.forecast_start.strftime("%Y-%m-%d") if cpm_node and cpm_node.forecast_start else p_start
            f_finish = cpm_node.forecast_finish.strftime("%Y-%m-%d") if cpm_node and cpm_node.forecast_finish else p_finish
            tot_float = cpm_node.total_float if cpm_node else a.total_float
            finish_var = cpm_node.finish_variance if cpm_node else 0.0
            is_crit = cpm_node.is_critical if cpm_node else (a.is_critical or (tot_float is not None and tot_float <= 0))

            # Filter by critical or delayed if requested
            if filters.get("is_critical") and not is_crit:
                continue
            if filters.get("has_zero_float") and (tot_float != 0.0):
                continue
            if filters.get("is_delayed"):
                # Delayed means forecast finish is after planned finish or overdue past data date
                is_past_due = (
                    project.data_date
                    and a.planned_finish
                    and a.planned_finish < project.data_date
                    and (a.percent_complete or 0.0) < 100.0
                )
                is_forecast_late = (finish_var is not None and finish_var > 0)
                if not (is_past_due or is_forecast_late):
                    continue

            results.append({
                "id": a.id,
                "activity_code": a.activity_code,
                "name": a.name,
                "status": a.status,
                "percent_complete": a.percent_complete or 0.0,
                "duration": a.original_duration or 0.0,
                "remaining_duration": a.remaining_duration,
                "planned_start": p_start,
                "planned_finish": p_finish,
                "forecast_start": f_start,
                "forecast_finish": f_finish,
                "total_float": tot_float,
                "finish_variance": finish_var,
                "is_critical": is_crit,
                "wbs_name": a.wbs_node.name if a.wbs_node else (a.discipline or "General"),
                "wbs_code": a.wbs_node.code if a.wbs_node else "N/A",
                "location_code": a.location_code,
                "contractor_name": a.contractor_name,
                "driving_predecessor": cpm_node.driving_predecessor_code if cpm_node else None,
            })

        limit = filters.get("limit", 50)
        return results[:limit]

    @classmethod
    def _resolve_single_activity(
        cls,
        db: Session,
        project: Project,
        identifier: str,
    ) -> Optional[Activity]:
        """Resolves an activity by exact code, exact name, ID, or case-insensitive substring."""
        if not identifier:
            return None
        ident_clean = identifier.strip()
        act = (
            db.query(Activity)
            .options(joinedload(Activity.wbs_node))
            .filter(
                Activity.project_id == project.id,
                or_(
                    func.lower(Activity.activity_code) == ident_clean.lower(),
                    func.lower(Activity.name) == ident_clean.lower(),
                    Activity.id == ident_clean,
                ),
            )
            .first()
        )
        if not act:
            act = (
                db.query(Activity)
                .options(joinedload(Activity.wbs_node))
                .filter(
                    Activity.project_id == project.id,
                    func.lower(Activity.name).contains(ident_clean.lower()),
                )
                .first()
            )
        return act

    @classmethod
    def get_activity_details(
        cls,
        db: Session,
        project: Project,
        identifier: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves comprehensive details and relationships for a specific activity."""
        act = cls._resolve_single_activity(db, project, identifier)
        if not act:
            return None

        cpm_res = cls.get_cpm_analysis(db, project)
        cpm_node = cpm_res.activities.get(act.id) or next(
            (n for n in cpm_res.activities.values() if n.activity_code == act.activity_code), None
        )

        # Incoming relationships (predecessors)
        preds_raw = (
            db.query(ActivityRelationship, Activity)
            .join(Activity, ActivityRelationship.predecessor_id == Activity.id)
            .filter(ActivityRelationship.successor_id == act.id)
            .all()
        )
        preds = [
            {
                "code": p.activity_code,
                "name": p.name,
                "type": r.relationship_type,
                "lag": r.lag,
            }
            for r, p in preds_raw
        ]

        # Outgoing relationships (successors)
        succs_raw = (
            db.query(ActivityRelationship, Activity)
            .join(Activity, ActivityRelationship.successor_id == Activity.id)
            .filter(ActivityRelationship.predecessor_id == act.id)
            .all()
        )
        succs = [
            {
                "code": s.activity_code,
                "name": s.name,
                "type": r.relationship_type,
                "lag": r.lag,
            }
            for r, s in succs_raw
        ]

        p_start = act.planned_start.strftime("%Y-%m-%d") if act.planned_start else "N/A"
        p_finish = act.planned_finish.strftime("%Y-%m-%d") if act.planned_finish else "N/A"
        f_start = cpm_node.forecast_start.strftime("%Y-%m-%d") if cpm_node and cpm_node.forecast_start else p_start
        f_finish = cpm_node.forecast_finish.strftime("%Y-%m-%d") if cpm_node and cpm_node.forecast_finish else p_finish

        return {
            "id": act.id,
            "activity_code": act.activity_code,
            "name": act.name,
            "status": act.status,
            "percent_complete": act.percent_complete or 0.0,
            "duration": act.original_duration or 0.0,
            "remaining_duration": act.remaining_duration,
            "planned_start": p_start,
            "planned_finish": p_finish,
            "forecast_start": f_start,
            "forecast_finish": f_finish,
            "total_float": cpm_node.total_float if cpm_node else act.total_float,
            "free_float": cpm_node.free_float if cpm_node else act.free_float,
            "finish_variance": cpm_node.finish_variance if cpm_node else 0.0,
            "is_critical": cpm_node.is_critical if cpm_node else act.is_critical,
            "wbs_name": act.wbs_node.name if act.wbs_node else (act.discipline or "General"),
            "wbs_code": act.wbs_node.code if act.wbs_node else "N/A",
            "driving_predecessor": cpm_node.driving_predecessor_code if cpm_node else None,
            "float_explanation": cpm_node.float_explanation if cpm_node else None,
            "predecessors": preds,
            "successors": succs,
        }

    @classmethod
    def get_dependencies(
        cls,
        db: Session,
        project: Project,
        identifier: Optional[str] = None,
        direction: str = "both",
    ) -> Dict[str, Any]:
        """
        Handles dependency inspection:
        - For a specific activity: predecessors, successors, and relationship types.
        - For the project network: open start activities (no predecessor) and open finish activities (no successor).
        """
        if identifier:
            details = cls.get_activity_details(db, project, identifier)
            if not details:
                return {"found": False, "identifier": identifier}
            return {
                "found": True,
                "activity_code": details["activity_code"],
                "activity_name": details["name"],
                "predecessors": details["predecessors"],
                "successors": details["successors"],
            }

        # Network topology analysis: Open starts and open finishes
        all_acts = db.query(Activity).filter(Activity.project_id == project.id).all()
        rels = RelationshipRepository.get_by_project(db, project.id)

        preds_set = {r.successor_id for r in rels}
        succs_set = {r.predecessor_id for r in rels}

        open_starts = [
            {"code": a.activity_code, "name": a.name}
            for a in all_acts
            if a.id not in preds_set
        ]
        open_finishes = [
            {"code": a.activity_code, "name": a.name}
            for a in all_acts
            if a.id not in succs_set
        ]

        return {
            "open_starts": open_starts,
            "open_finishes": open_finishes,
        }

    @classmethod
    def get_critical_path(cls, db: Session, project: Project) -> Dict[str, Any]:
        """Returns the complete CPM critical path and float metrics."""
        cpm_res = cls.get_cpm_analysis(db, project)

        critical_nodes = [
            n for n in cpm_res.activities.values() if n.is_critical
        ]
        # Order by early start
        critical_nodes.sort(key=lambda x: x.early_start or date.min)

        return {
            "critical_path_sequence": cpm_res.critical_path,
            "critical_activities_count": len(critical_nodes),
            "project_duration_days": cpm_res.project_duration_days,
            "calculated_finish": cpm_res.project_finish.strftime("%Y-%m-%d") if cpm_res.project_finish else "N/A",
            "critical_activities": [
                {
                    "code": n.activity_code,
                    "name": n.name,
                    "early_start": n.early_start.strftime("%Y-%m-%d") if n.early_start else "N/A",
                    "early_finish": n.early_finish.strftime("%Y-%m-%d") if n.early_finish else "N/A",
                    "total_float": n.total_float,
                    "driving_predecessor": n.driving_predecessor_code,
                }
                for n in critical_nodes
            ],
            "near_critical_count": len(cpm_res.near_critical_activities),
            "negative_float_count": len(cpm_res.negative_float_activities),
        }

    @classmethod
    def get_delayed_activities(
        cls,
        db: Session,
        project: Project,
        min_days: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Identifies activities that are overdue or forecast to finish late."""
        return cls.search_activities(db, project, {"is_delayed": True})

    @classmethod
    def get_date_window_activities(
        cls,
        db: Session,
        project: Project,
        window_type: str,
    ) -> Dict[str, Any]:
        """
        Calculates activities falling into relative temporal windows
        (due today, due tomorrow, starts this week, finishes this month, etc.)
        anchored on the project's data_date.
        """
        ref_date = project.data_date.date() if project.data_date else datetime.now(timezone.utc).date()
        acts = db.query(Activity).filter(Activity.project_id == project.id).all()

        results = []
        label = window_type.replace("_", " ").title()

        for a in acts:
            p_start = a.planned_start.date() if a.planned_start else None
            p_finish = a.planned_finish.date() if a.planned_finish else None
            is_match = False
            reason = ""

            if window_type == "due_today":
                if p_finish == ref_date:
                    is_match = True
                    reason = f"Due today ({ref_date})"
            elif window_type == "due_tomorrow":
                tom = ref_date + timedelta(days=1)
                if p_finish == tom:
                    is_match = True
                    reason = f"Due tomorrow ({tom})"
            elif window_type in ("starts_this_week", "this_week"):
                week_end = ref_date + timedelta(days=7)
                if p_start and ref_date <= p_start <= week_end:
                    is_match = True
                    reason = f"Starts {p_start}"
                elif p_finish and ref_date <= p_finish <= week_end:
                    is_match = True
                    reason = f"Finishes {p_finish}"
            elif window_type == "starts_next_week":
                w1 = ref_date + timedelta(days=7)
                w2 = ref_date + timedelta(days=14)
                if p_start and w1 <= p_start <= w2:
                    is_match = True
                    reason = f"Starts {p_start}"
            elif window_type == "finishes_this_month":
                m_end = ref_date + timedelta(days=30)
                if p_finish and ref_date <= p_finish <= m_end:
                    is_match = True
                    reason = f"Finishes {p_finish}"
            elif window_type == "overdue":
                if p_finish and p_finish < ref_date and (a.percent_complete or 0.0) < 100.0:
                    is_match = True
                    days_over = (ref_date - p_finish).days
                    reason = f"{days_over} days overdue (Planned: {p_finish})"

            if is_match:
                results.append({
                    "activity_code": a.activity_code,
                    "name": a.name,
                    "status": a.status,
                    "percent_complete": a.percent_complete or 0.0,
                    "planned_start": p_start.strftime("%Y-%m-%d") if p_start else "N/A",
                    "planned_finish": p_finish.strftime("%Y-%m-%d") if p_finish else "N/A",
                    "timing_note": reason,
                })

        return {
            "window_type": window_type,
            "label": label,
            "reference_date": ref_date.strftime("%Y-%m-%d"),
            "count": len(results),
            "activities": results,
        }

    @classmethod
    def get_statistics(
        cls,
        db: Session,
        project: Project,
        wbs_or_discipline: Optional[str] = None,
        activity_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Calculates project aggregations, duration statistics, and departmental distributions."""
        query = db.query(Activity).options(joinedload(Activity.wbs_node)).filter(Activity.project_id == project.id)

        if activity_ids:
            query = query.filter(Activity.id.in_(activity_ids))
        elif wbs_or_discipline:
            term = str(wbs_or_discipline).strip().lower()
            query = query.outerjoin(WBSNode, Activity.wbs_id == WBSNode.id).filter(
                or_(
                    func.lower(WBSNode.name).contains(term),
                    func.lower(WBSNode.code).contains(term),
                    func.lower(Activity.discipline).contains(term),
                    func.lower(Activity.name).contains(term),
                )
            )

        activities = query.all()
        total_count = len(activities)

        if total_count == 0:
            return {
                "scope": wbs_or_discipline or "Project",
                "total_activities": 0,
                "completed": 0,
                "in_progress": 0,
                "not_started": 0,
                "progress_weighted": 0.0,
                "progress_average": 0.0,
                "total_duration": 0.0,
                "longest_activity": None,
                "shortest_activity": None,
            }

        completed = [a for a in activities if a.status == "COMPLETED" or (a.percent_complete or 0) >= 100.0]
        in_progress = [a for a in activities if a.status == "IN_PROGRESS" and (a.percent_complete or 0) < 100.0]
        not_started = [a for a in activities if a.status == "NOT_STARTED" and (a.percent_complete or 0) == 0.0]

        total_dur = sum(a.original_duration or 0.0 for a in activities)
        comp_dur = sum((a.original_duration or 0.0) * ((a.percent_complete or 0.0) / 100.0) for a in activities)
        prog_weighted = round((comp_dur / total_dur * 100.0), 1) if total_dur > 0 else 0.0
        prog_avg = round(sum(a.percent_complete or 0.0 for a in activities) / total_count, 1)

        longest = max(activities, key=lambda a: a.original_duration or 0.0)
        shortest = min(activities, key=lambda a: a.original_duration or 0.0)

        # Department / WBS breakdown across all project activities
        all_acts = (
            db.query(Activity)
            .options(joinedload(Activity.wbs_node))
            .filter(Activity.project_id == project.id)
            .all()
        )
        wbs_map: Dict[str, Dict[str, Any]] = {}
        for a in all_acts:
            name = a.wbs_node.name if a.wbs_node else (a.discipline or "General")
            if name not in wbs_map:
                wbs_map[name] = {"name": name, "count": 0, "incomplete": 0}
            wbs_map[name]["count"] += 1
            if a.status != "COMPLETED" and (a.percent_complete or 0.0) < 100.0:
                wbs_map[name]["incomplete"] += 1

        wbs_sorted = sorted(wbs_map.values(), key=lambda x: x["count"], reverse=True)
        dept_most_acts = wbs_sorted[0]["name"] if wbs_sorted else "N/A"
        wbs_most_incomplete = (
            sorted(wbs_map.values(), key=lambda x: x["incomplete"], reverse=True)[0]["name"]
            if wbs_sorted else "N/A"
        )

        return {
            "scope": wbs_or_discipline or "Project",
            "total_activities": total_count,
            "completed": len(completed),
            "in_progress": len(in_progress),
            "not_started": len(not_started),
            "progress_weighted": prog_weighted,
            "progress_average": prog_avg,
            "total_duration": total_dur,
            "average_duration": round(total_dur / total_count, 1),
            "longest_activity": {
                "code": longest.activity_code,
                "name": longest.name,
                "duration": longest.original_duration or 0.0,
            },
            "shortest_activity": {
                "code": shortest.activity_code,
                "name": shortest.name,
                "duration": shortest.original_duration or 0.0,
            },
            "department_with_most_activities": dept_most_acts,
            "wbs_with_most_incomplete_work": wbs_most_incomplete,
            "wbs_distribution": wbs_sorted,
        }

    @classmethod
    def simulate_delay(
        cls,
        db: Session,
        project: Project,
        identifier: str,
        delay_days: float,
    ) -> Dict[str, Any]:
        """
        Simulates network impact of delaying an activity by N working days.
        Strictly read-only; leaves PostgreSQL database untouched.
        """
        act = cls._resolve_single_activity(db, project, identifier)
        target_code = act.activity_code if act else identifier
        from app.services.agent_service import TimeAgentService
        return TimeAgentService._simulate_activity_delay(db, project, target_code, delay_days)

    # -------------------------------------------------------------------------
    # 2. QUERY UNDERSTANDING & ROUTING
    # -------------------------------------------------------------------------

    @classmethod
    def execute_query(
        cls,
        db: Session,
        project: Project,
        conv: Conversation,
        user_query: str,
        conversation_context: Optional[Dict[str, Any]] = None,
        language: str = "en",
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Main query entrypoint:
        1. Identifies intent, entities, filters, and tool.
        2. Detects conversational follow-up references.
        3. Executes structured database query and aggregations.
        4. Formats clean, natural-language response.
        5. Returns (reply_text, new_query_context).
        """
        clean_query = user_query.strip()
        lower = clean_query.lower()

        # Step 1: Check prior turn context for conversational follow-ups
        prior_context = conversation_context or cls._extract_prior_query_context(db, conv)
        scoped_activity_ids = None
        is_follow_up = False

        anaphoric_phrases = [
            "which ones", "which one", "which of these", "which of them", "among them",
            "how many of them", "how many are", "how many of these", "inme se", "unme se",
            "kaunse", "kaunsa", "kitne hain", "kitni hain", "which are", "which has"
        ]
        has_anaphora = any(p in lower for p in anaphoric_phrases)
        extracted_disc = cls._extract_discipline_or_wbs(lower)

        has_new_command = any(k in lower for k in [
            "give me all", "show all", "list all", "what activities", "show activities",
            "list activities", "all activities", "what is completed", "what is pending",
            "what is currently in progress", "which activities are overdue"
        ])

        if prior_context and (has_anaphora or (len(clean_query.split()) <= 6 and not extracted_disc and not has_new_command)):
            is_follow_up = True
            if has_anaphora and prior_context.get("matched_activity_ids"):
                scoped_activity_ids = prior_context["matched_activity_ids"]

        # Step 2: Route to appropriate structured tool
        routing_info = cls._route_query(lower, prior_context, is_follow_up)

        detected_tool = routing_info["tool"]
        tool_args = routing_info["args"]

        if is_follow_up and scoped_activity_ids:
            tool_args["activity_ids"] = scoped_activity_ids

        # Logging for development / debugging (Requirement 23)
        logger.info(
            f"\n--- [TIME AGENT QUERY ENGINE] ---\n"
            f"Query: {user_query}\n"
            f"Detected Intent: {routing_info.get('intent')}\n"
            f"Detected Entity: {routing_info.get('entity')}\n"
            f"Detected Filters: {tool_args}\n"
            f"Selected Tool: {detected_tool}\n"
            f"Is Follow-Up: {is_follow_up}\n"
            f"---------------------------------"
        )

        # Step 3: Execute tool against real PostgreSQL database
        result_data: Any = None
        matched_activities: List[Dict[str, Any]] = []

        if detected_tool == "SIMULATE_DELAY":
            sim_target = tool_args.get("identifier") or (prior_context.get("target_activity_code") if prior_context else None)
            delay_val = float(tool_args.get("delay_days", 5.0))
            if sim_target:
                result_data = cls.simulate_delay(db, project, sim_target, delay_val)
            else:
                result_data = {"error": "No target activity specified for simulation."}

        elif detected_tool == "CRITICAL_PATH":
            result_data = cls.get_critical_path(db, project)
            matched_activities = result_data.get("critical_activities", [])

        elif detected_tool == "DEPENDENCIES":
            ident = tool_args.get("identifier")
            direction = tool_args.get("direction", "both")
            result_data = cls.get_dependencies(db, project, identifier=ident, direction=direction)

        elif detected_tool == "DATE_WINDOW":
            w_type = tool_args.get("window_type", "this_week")
            result_data = cls.get_date_window_activities(db, project, w_type)
            matched_activities = result_data.get("activities", [])

        elif detected_tool == "STATISTICS":
            disc = tool_args.get("wbs_or_discipline")
            act_ids = tool_args.get("activity_ids")
            result_data = cls.get_statistics(db, project, wbs_or_discipline=disc, activity_ids=act_ids)

        elif detected_tool == "PROJECT_SUMMARY":
            result_data = cls.get_project_summary(db, project)

        elif detected_tool == "ACTIVITY_DETAILS":
            ident = tool_args.get("identifier")
            result_data = cls.get_activity_details(db, project, ident)
            if result_data:
                matched_activities = [result_data]

        else:  # Default to SEARCH_ACTIVITIES
            matched_activities = cls.search_activities(db, project, tool_args)
            result_data = {
                "count": len(matched_activities),
                "activities": matched_activities,
                "filters": tool_args,
            }

        # Step 4: Format Natural-Language Answer
        reply_text = cls._format_response(
            tool=detected_tool,
            data=result_data,
            query=clean_query,
            project=project,
            language=language,
            tool_args=tool_args,
        )

        # Step 5: Construct new conversation context for future follow-up turns
        new_context = {
            "last_tool": detected_tool,
            "last_query": clean_query,
            "matched_activity_ids": [a["id"] for a in matched_activities if "id" in a],
            "matched_activity_codes": [a["activity_code"] for a in matched_activities if "activity_code" in a],
            "wbs_or_discipline": tool_args.get("wbs_or_discipline"),
            "target_activity_code": tool_args.get("identifier"),
        }

        return reply_text, new_context

    # -------------------------------------------------------------------------
    # 3. SEMANTIC ROUTING LOGIC
    # -------------------------------------------------------------------------

    @classmethod
    def _route_query(
        cls,
        lower: str,
        prior_context: Optional[Dict[str, Any]],
        is_follow_up: bool,
    ) -> Dict[str, Any]:
        """Classifies the user query into intent, target entity, filters, and tool."""
        # 1. Check for delay simulation
        delay_match = re.search(
            r"(?:delay(?:ed)?\s+(?:by\s+)?(\d+(?:\.\d+)?)|what\s+if.*?(\d+(?:\.\d+)?)\s*d(?:ays?)?|what\s+happens\s+if.*?(\d+(?:\.\d+)?)\s*d(?:ays?)?)",
            lower,
        )
        if delay_match:
            delay_val = float(delay_match.group(1) or delay_match.group(2) or delay_match.group(3) or 5.0)
            target = cls._extract_activity_mention(lower)
            return {
                "intent": "DELAY_SIMULATION",
                "entity": "ACTIVITY",
                "tool": "SIMULATE_DELAY",
                "args": {"identifier": target, "delay_days": delay_val},
            }

        # 2. Critical path & float questions
        if any(k in lower for k in [
            "critical path", "longest path", "critical activities", "zero float",
            "least float", "controlling completion", "critical tasks",
            "are critical", "is critical", "show critical"
        ]):
            if "critical path" in lower or "longest path" in lower:
                return {
                    "intent": "CRITICAL_PATH_QUERY",
                    "entity": "PROJECT",
                    "tool": "CRITICAL_PATH",
                    "args": {},
                }
            if "zero float" in lower:
                return {
                    "intent": "FLOAT_QUERY",
                    "entity": "ACTIVITY",
                    "tool": "SEARCH_ACTIVITIES",
                    "args": {"has_zero_float": True},
                }
            if "least float" in lower:
                return {
                    "intent": "FLOAT_QUERY",
                    "entity": "ACTIVITY",
                    "tool": "SEARCH_ACTIVITIES",
                    "args": {"least_float": True, "limit": 5},
                }
            return {
                "intent": "CRITICAL_ACTIVITIES_QUERY",
                "entity": "ACTIVITY",
                "tool": "SEARCH_ACTIVITIES",
                "args": {"is_critical": True},
            }

        # 3. Dependencies & sequence questions
        if any(k in lower for k in [
            "comes after", "come after", "after", "depends on", "depend on",
            "predecessor", "successor", "next activity", "previous activity",
            "no predecessor", "no successor", "open start", "open finish"
        ]):
            if any(k in lower for k in ["no predecessor", "open start"]):
                return {
                    "intent": "DEPENDENCY_TOPOLOGY",
                    "entity": "NETWORK",
                    "tool": "DEPENDENCIES",
                    "args": {"direction": "open_starts"},
                }
            if any(k in lower for k in ["no successor", "open finish"]):
                return {
                    "intent": "DEPENDENCY_TOPOLOGY",
                    "entity": "NETWORK",
                    "tool": "DEPENDENCIES",
                    "args": {"direction": "open_finishes"},
                }
            target = cls._extract_activity_mention(lower)
            direction = "successors" if any(k in lower for k in ["after", "successor", "depend on", "depends on"]) else "predecessors"
            return {
                "intent": "DEPENDENCY_QUERY",
                "entity": "ACTIVITY",
                "tool": "DEPENDENCIES",
                "args": {"identifier": target, "direction": direction},
            }

        # 4. Temporal / Date-window questions
        if any(k in lower for k in ["due today", "due tomorrow", "this week", "next week", "this month", "overdue"]):
            w_type = "this_week"
            if "today" in lower:
                w_type = "due_today"
            elif "tomorrow" in lower:
                w_type = "due_tomorrow"
            elif "next week" in lower:
                w_type = "starts_next_week"
            elif "this month" in lower:
                w_type = "finishes_this_month"
            elif "overdue" in lower:
                w_type = "overdue"
            return {
                "intent": "DATE_WINDOW_QUERY",
                "entity": "SCHEDULE",
                "tool": "DATE_WINDOW",
                "args": {"window_type": w_type},
            }

        # 5. Statistics / Aggregation questions
        is_stat_query = any(k in lower for k in [
            "how many", "percentage", "percent complete", "total duration",
            "longest duration", "shortest duration", "average progress",
            "which department has the most", "which wbs has the most",
            "completion percentage", "kitne", "kitni"
        ])

        extracted_disc = cls._extract_discipline_or_wbs(lower)

        if is_stat_query:
            if "longest" in lower:
                return {
                    "intent": "STATISTICS_LONGEST",
                    "entity": "ACTIVITY",
                    "tool": "SEARCH_ACTIVITIES",
                    "args": {
                        "wbs_or_discipline": extracted_disc or (prior_context.get("wbs_or_discipline") if is_follow_up else None),
                        "longest_duration": True,
                        "limit": 1,
                    },
                }
            if "shortest" in lower:
                return {
                    "intent": "STATISTICS_SHORTEST",
                    "entity": "ACTIVITY",
                    "tool": "SEARCH_ACTIVITIES",
                    "args": {
                        "wbs_or_discipline": extracted_disc or (prior_context.get("wbs_or_discipline") if is_follow_up else None),
                        "shortest_duration": True,
                        "limit": 1,
                    },
                }
            return {
                "intent": "STATISTICS_QUERY",
                "entity": "PROJECT",
                "tool": "STATISTICS",
                "args": {
                    "wbs_or_discipline": extracted_disc or (prior_context.get("wbs_or_discipline") if is_follow_up else None),
                },
            }

        # 6. Project Summary / Progress Overview / Finish Date
        if any(k in lower for k in [
            "summary of this project", "summary of project", "project summary",
            "how is the project progressing", "project progress", "overall status",
            "what is the status of the project", "biggest schedule risks", "what needs attention",
            "when will the project finish", "when is the project expected to finish",
            "expected finish", "finish date", "project finish", "completion date"
        ]):
            return {
                "intent": "PROJECT_SUMMARY",
                "entity": "PROJECT",
                "tool": "PROJECT_SUMMARY",
                "args": {},
            }

        # 7. Status filtering (completed, in progress, pending, delayed)
        status_filter = None
        if "incomplete" in lower:
            status_filter = "INCOMPLETE"
        elif any(k in lower for k in ["completed", "complete", "finished", "done", "poora", "khatam"]):
            status_filter = "COMPLETED"
        elif any(k in lower for k in ["in progress", "ongoing", "active", "chal raha"]):
            status_filter = "IN_PROGRESS"
        elif any(k in lower for k in ["not started", "pending", "planned", "shuru nahi"]):
            status_filter = "NOT_STARTED"

        is_delayed_filter = any(k in lower for k in ["delayed", "delay", "late", "behind schedule", "overdue", "causing delay"])

        # 8. Specific activity code cited
        act_code = cls._extract_activity_code(lower)
        if act_code:
            return {
                "intent": "ACTIVITY_DETAILS",
                "entity": "ACTIVITY",
                "tool": "ACTIVITY_DETAILS",
                "args": {"identifier": act_code},
            }

        # 9. General Activity Search with filters
        kw = None
        if not extracted_disc and not is_follow_up:
            candidate_kw = cls._extract_clean_keyword(lower)
            if candidate_kw and candidate_kw.lower() not in [
                "completed", "complete", "in progress", "pending", "not started",
                "delayed", "delay", "overdue", "status", "progress", "incomplete"
            ]:
                kw = candidate_kw

        active_disc = extracted_disc or (prior_context.get("wbs_or_discipline") if is_follow_up else None)

        return {
            "intent": "ACTIVITY_SEARCH",
            "entity": "ACTIVITY",
            "tool": "SEARCH_ACTIVITIES",
            "args": {
                "wbs_or_discipline": active_disc,
                "status": status_filter,
                "is_delayed": is_delayed_filter,
                "keyword": kw,
            },
        }

    # -------------------------------------------------------------------------
    # 4. RESPONSE FORMATTERS
    # -------------------------------------------------------------------------

    @classmethod
    def _format_response(
        cls,
        tool: str,
        data: Any,
        query: str,
        project: Project,
        language: str,
        tool_args: Dict[str, Any],
    ) -> str:
        """Formats clean, readable responses based on tool output and conversation language."""
        is_hi = language.startswith("hi")
        is_hinglish = language == "hinglish"

        # 0. Simulate Delay
        if tool == "SIMULATE_DELAY":
            sim = data
            if not sim or "error" in sim:
                return f"Unable to simulate delay: {sim.get('error', 'Target activity not identified.') if sim else 'Unknown error'}."
            return (
                f"**[Deterministic Schedule Simulation — Read-Only]**\n\n"
                f"Simulating **+{sim.get('delay_days', 0)}d** duration delay on **{sim.get('activity_code', '')}** ({sim.get('activity_name', '')}):\n"
                f"• **Baseline Project Finish:** {sim.get('base_project_finish')}\n"
                f"• **Simulated Project Finish:** {sim.get('sim_project_finish')}\n"
                f"• **Net Project Delay Impact:** **+{sim.get('impact_days', 0)}d** to completion\n"
                f"• **Activity Total Float:** {sim.get('sim_float')}d (was {sim.get('base_float')}d)\n"
                f"• **Critical After Delay:** {'Yes' if sim.get('is_critical_after') else 'No'}\n\n"
                f"*Note: Official project schedule records remain strictly unchanged.*"
            )

        # 1. Project Summary
        if tool == "PROJECT_SUMMARY":
            d = data
            if is_hi:
                return (
                    f"**प्रोजेक्ट सारांश: {d['project_name']} ({d['project_code']})**\n\n"
                    f"• **कुल प्रगति:** {d['progress_weighted']}% (Duration-weighted), {d['progress_simple']}% (Simple avg)\n"
                    f"• **कुल गतिविधियाँ:** {d['activity_count']} (पूर्ण: {d['completed_count']}, प्रगति में: {d['in_progress_count']}, शुरू नहीं: {d['not_started_count']})\n"
                    f"• **Data Date:** {d['data_date']}\n"
                    f"• **योजनाबद्ध समाप्ति:** {d['planned_finish']}\n"
                    f"• **पूर्वानुमानित समाप्ति (CPM):** {d['forecast_finish']} ({d['cpm_duration_days']} कार्य दिवस)\n"
                    f"• **क्रिटिकल गतिविधियाँ:** {d['critical_activities_count']}\n"
                    f"• **Negative Float:** {d['negative_float_count']} गतिविधियाँ"
                )
            if is_hinglish:
                return (
                    f"**Project Summary: {d['project_name']} ({d['project_code']})**\n\n"
                    f"• **Total Progress:** {d['progress_weighted']}% (Duration-weighted), {d['progress_simple']}% (Simple avg)\n"
                    f"• **Total Activities:** {d['activity_count']} (Completed: {d['completed_count']}, In Progress: {d['in_progress_count']}, Not Started: {d['not_started_count']})\n"
                    f"• **Data Date:** {d['data_date']}\n"
                    f"• **Planned Finish:** {d['planned_finish']}\n"
                    f"• **Forecast Finish (CPM):** {d['forecast_finish']} ({d['cpm_duration_days']} working days)\n"
                    f"• **Critical Activities:** {d['critical_activities_count']}\n"
                    f"• **Negative Float Activities:** {d['negative_float_count']}"
                )
            return (
                f"**Project Summary for {d['project_name']} ({d['project_code']}):**\n\n"
                f"• **Overall Completion:** **{d['progress_weighted']}%** (duration-weighted) / {d['progress_simple']}% (simple average)\n"
                f"• **Activity Status:** {d['activity_count']} total ({d['completed_count']} completed, {d['in_progress_count']} in progress, {d['not_started_count']} not started)\n"
                f"• **Data Date:** {d['data_date']}\n"
                f"• **Planned Finish:** {d['planned_finish']}\n"
                f"• **Forecast Finish (CPM):** {d['forecast_finish']} ({d['cpm_duration_days']} working days)\n"
                f"• **Critical Path:** {d['critical_activities_count']} critical activities"
                + (f"\n• **Schedule Warning:** {d['negative_float_count']} negative float activities requiring attention." if d['negative_float_count'] > 0 else "")
            )

        # 2. Critical Path
        if tool == "CRITICAL_PATH":
            d = data
            seq = " ➔ ".join(d["critical_path_sequence"]) if d["critical_path_sequence"] else "None"
            acts = d.get("critical_activities", [])
            lines = [f"{i+1}. **{a['code']}** ({a['name']}) — Float: {a['total_float']}d" for i, a in enumerate(acts[:10])]
            list_txt = "\n".join(lines)
            if is_hi:
                return (
                    f"**प्रोजेक्ट क्रिटिकल पाथ विश्लेषण ({project.project_code}):**\n\n"
                    f"• **कुल क्रिटिकल गतिविधियाँ:** {d['critical_activities_count']}\n"
                    f"• **परियोजना अवधि:** {d['project_duration_days']} कार्य दिवस\n"
                    f"• **अपेक्षित समाप्ति:** {d['calculated_finish']}\n"
                    f"• **क्रिटिकल सीक्वेंस:** {seq}\n\n"
                    f"**मुख्य क्रिटिकल गतिविधियाँ:**\n{list_txt}"
                )
            return (
                f"**Critical Path Analysis for {project.project_code}:**\n\n"
                f"• **Total Duration:** **{d['project_duration_days']} working days**\n"
                f"• **Projected Finish Date:** **{d['calculated_finish']}**\n"
                f"• **Critical Path Sequence:**\n  {seq}\n\n"
                f"**Critical Activities ({d['critical_activities_count']} total with zero/minimum float):**\n"
                f"{list_txt}"
            )

        # 3. Dependencies
        if tool == "DEPENDENCIES":
            d = data
            if not d.get("found", True):
                return f"Activity '{d.get('identifier')}' was not found in project '{project.project_code}'."

            if "activity_code" in d:
                preds = d.get("predecessors", [])
                succs = d.get("successors", [])

                p_str = ", ".join([f"{p['code']} ({p['name']} - {p['type']})" for p in preds]) if preds else "None (Project Start)"
                s_str = ", ".join([f"{s['code']} ({s['name']} - {s['type']})" for s in succs]) if succs else "None (Terminal Task)"

                return (
                    f"**Dependency Relationships for {d['activity_code']} ({d['activity_name']}):**\n\n"
                    f"• **Predecessors (Comes Before):**\n  {p_str}\n"
                    f"• **Successors (Comes After / Depends On):**\n  {s_str}"
                )

            # Topology
            starts = ", ".join([f"{a['code']} ({a['name']})" for a in d.get("open_starts", [])]) or "None"
            finishes = ", ".join([f"{a['code']} ({a['name']})" for a in d.get("open_finishes", [])]) or "None"
            return (
                f"**Schedule Network Boundaries for {project.project_code}:**\n\n"
                f"• **Open Start Activities (No Predecessors):**\n  {starts}\n\n"
                f"• **Open Finish Activities (No Successors):**\n  {finishes}"
            )

        # 4. Temporal / Date Window
        if tool == "DATE_WINDOW":
            d = data
            acts = d.get("activities", [])
            if not acts:
                return f"No activities found for **{d['label']}** (Reference Data Date: {d['reference_date']})."

            lines = [
                f"{i+1}. **{a['activity_code']}**: {a['name']} — {a['timing_note']} ({a['status']}, {a['percent_complete']}%)"
                for i, a in enumerate(acts[:12])
            ]
            return (
                f"**Activities for {d['label']} ({d['count']} total, Data Date: {d['reference_date']}):**\n\n"
                + "\n".join(lines)
            )

        # 5. Statistics
        if tool == "STATISTICS":
            d = data
            scope_name = d["scope"]
            lower_q = query.lower()
            lead_in = ""
            if "completed" in lower_q and ("how many" in lower_q or "kitne" in lower_q or "kitni" in lower_q):
                lead_in = f"There {'is' if d['completed'] == 1 else 'are'} **{d['completed']} completed** {'activity' if d['completed'] == 1 else 'activities'} out of {d['total_activities']} total in {scope_name}.\n\n"
            elif "how many" in lower_q or "kitne" in lower_q or "kitni" in lower_q:
                lead_in = f"There {'is' if d['total_activities'] == 1 else 'are'} **{d['total_activities']} {scope_name}** {'activity' if d['total_activities'] == 1 else 'activities'} in project {project.project_code}.\n\n"

            return (
                f"{lead_in}**Statistics for {scope_name} ({project.project_code}):**\n\n"
                f"• **Total Activities:** {d['total_activities']}\n"
                f"• **Status Breakdown:** {d['completed']} completed, {d['in_progress']} in progress, {d['not_started']} not started\n"
                f"• **Overall Progress:** **{d['progress_weighted']}%** (duration-weighted), {d['progress_average']}% (simple average)\n"
                f"• **Total Duration:** {d['total_duration']} working days (Average: {d['average_duration']} days/activity)\n"
                + (f"• **Longest Activity:** **{d['longest_activity']['code']}** ({d['longest_activity']['name']}) — {d['longest_activity']['duration']} days\n" if d['longest_activity'] else "")
                + (f"• **Shortest Activity:** **{d['shortest_activity']['code']}** ({d['shortest_activity']['name']}) — {d['shortest_activity']['duration']} days\n" if d['shortest_activity'] else "")
                + f"• **Department with Most Activities:** {d['department_with_most_activities']}\n"
                + f"• **WBS with Most Incomplete Work:** {d['wbs_with_most_incomplete_work']}"
            )

        # 6. Activity Details
        if tool == "ACTIVITY_DETAILS":
            a = data
            if not a:
                return f"No matching activity found in project '{project.project_code}'."
            crit_label = "Yes (Longest Path)" if a["is_critical"] else "No"
            return (
                f"**Activity Details for {a['activity_code']} ({a['name']}):**\n\n"
                f"• **Status:** {a['status']} ({a['percent_complete']}% complete)\n"
                f"• **Duration:** {a['duration']} working days (Remaining: {a['remaining_duration'] or 0}d)\n"
                f"• **Planned Dates:** {a['planned_start']} to {a['planned_finish']}\n"
                f"• **Forecast Dates (CPM):** {a['forecast_start']} to {a['forecast_finish']}\n"
                f"• **Total Float:** {a['total_float']}d | **Finish Variance:** {a['finish_variance']:+.0f}d\n"
                f"• **Critical:** {crit_label}\n"
                f"• **WBS / Discipline:** {a['wbs_name']} ({a['wbs_code']})\n"
                f"• **Driving Predecessor:** {a['driving_predecessor'] or 'None (Project Start)'}"
            )

        # 7. Search Activities List / Aggregation
        acts = data.get("activities", [])
        count = len(acts)
        scope = tool_args.get("wbs_or_discipline") or "matching"

        if count == 0:
            status_desc = f" with status {tool_args['status']}" if tool_args.get("status") else ""
            return f"I found **0 activities** in {scope}{status_desc} for project '{project.project_code}'."

        if tool_args.get("longest_duration") and count == 1:
            top = acts[0]
            return (
                f"**{top['activity_code']}** ({top['name']}) has the longest duration in {scope} at "
                f"**{top['duration']} working days** (Planned: {top['planned_start']} to {top['planned_finish']}, {top['percent_complete']}% complete)."
            )

        if tool_args.get("shortest_duration") and count == 1:
            top = acts[0]
            return (
                f"**{top['activity_code']}** ({top['name']}) has the shortest duration in {scope} at "
                f"**{top['duration']} working days** (Planned: {top['planned_start']} to {top['planned_finish']}, {top['percent_complete']}% complete)."
            )

        # Natural list display
        lines = []
        for i, a in enumerate(acts[:15]):
            crit_badge = " ⚡ [Critical]" if a.get("is_critical") else ""
            float_str = f", Float: {a['total_float']}d" if a.get("total_float") is not None else ""
            lines.append(
                f"{i+1}. **{a['activity_code']}**: {a['name']} — **{a['percent_complete']}% complete** "
                f"({a['status'].replace('_', ' ').title()}, {a['duration']}d duration, Planned: {a['planned_start']} to {a['planned_finish']}{float_str}){crit_badge}"
            )

        overflow_note = f"\n\n*(Showing top 15 of {count} activities)*" if count > 15 else ""

        prefix = f"I found **{count} {scope.title()}** activities in project '{project.project_code}':"
        return f"{prefix}\n\n" + "\n".join(lines) + overflow_note

    # -------------------------------------------------------------------------
    # 5. HELPER EXTRACTION UTILITIES
    # -------------------------------------------------------------------------

    @classmethod
    def _extract_prior_query_context(cls, db: Session, conv: Conversation) -> Optional[Dict[str, Any]]:
        """Retrieves query_context from the most recent agent message."""
        last_msgs = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conv.id, ConversationMessage.sender == "AGENT")
            .order_by(ConversationMessage.created_at.desc())
            .limit(5)
            .all()
        )
        for m in last_msgs:
            if m.message_metadata:
                try:
                    meta = json.loads(m.message_metadata) if isinstance(m.message_metadata, str) else m.message_metadata
                    if meta.get("query_context"):
                        return meta["query_context"]
                except Exception:
                    pass
        return None

    @classmethod
    def _extract_activity_code(cls, text: str) -> Optional[str]:
        """Extracts activity codes formatted like ENG-101, CIV-202, STR-301, A1020, F-204."""
        # 1. Standard pattern: CIV-101, MEC-203, F-204
        m = re.search(r"\b([A-Za-z0-9]{1,5}-[A-Za-z0-9]{2,6})\b", text)
        if m:
            return m.group(1).upper()
        # 2. P6 compact pattern: A1020, C1010
        p6_m = re.search(r"\b([A-Za-z]\d{3,6})\b", text)
        if p6_m:
            return p6_m.group(1).upper()
        # 3. Explicit "activity <name/code>"
        act_m = re.search(r"(?:activity|task|code)\s+([A-Za-z0-9-_]+)", text, re.I)
        if act_m:
            cand = act_m.group(1).strip()
            if cand.lower() not in ("code", "name", "id", "details", "status"):
                return cand.upper()
        return None

    @classmethod
    def _extract_activity_mention(cls, text: str) -> Optional[str]:
        """Extracts named activities or codes from questions like 'What comes after Foundation?'"""
        code = cls._extract_activity_code(text)
        if code:
            return code

        # Match phrases like 'what happens if Foundation is delayed', 'after Foundation', 'depend on Excavation', etc.
        m = re.search(
            r"(?:what happens if|what if|if|after|before|predecessor of|successor of|depends on|depend on|impact of)\s+"
            r"([A-Za-z0-9\s-]+?)"
            r"(?:\s+is|\s+gets|\s+delayed|\s+to|\s+by|\?|$)",
            text,
            re.IGNORECASE,
        )
        if m:
            cand = m.group(1).strip()
            # Remove filler words
            clean = re.sub(r"^(the|an|a|activity|task)\s+", "", cand, flags=re.I).strip()
            if clean:
                return clean
        return None

    @classmethod
    def _extract_discipline_or_wbs(cls, text: str) -> Optional[str]:
        """Extracts common construction disciplines or WBS keywords."""
        disciplines = [
            "mechanical", "electrical", "civil", "structural", "piping",
            "commissioning", "engineering", "instrumentation", "hvac",
            "plumbing", "procurement", "erection", "concrete"
        ]
        for d in disciplines:
            if re.search(rf"\b{d}\b", text):
                return d.capitalize()
        # WBS pattern e.g. "WBS 1.2" or "WBS-1.2"
        wbs_m = re.search(r"wbs[- ]?([0-9.]+)", text, re.I)
        if wbs_m:
            return wbs_m.group(1)
        return None

    @classmethod
    def _extract_clean_keyword(cls, text: str) -> Optional[str]:
        """Extracts non-conversational search keywords."""
        stop_words = {
            "give", "me", "all", "the", "activities", "activity", "in", "show", "list",
            "what", "is", "are", "which", "under", "belong", "to", "work", "tasks",
            "tell", "how", "many", "there", "do", "we", "have", "please", "can", "you",
            "ones", "one", "delayed", "delay", "completed", "complete", "pending", "overdue",
            "due", "progress", "active", "status", "of", "about", "related", "with", "for",
            "them", "these", "among", "those", "currently", "started", "not", "ongoing",
            "incomplete", "finished", "done", "causing"
        }
        tokens = [t.strip(".,?!") for t in text.split() if t.strip(".,?!").lower() not in stop_words]
        if tokens:
            return " ".join(tokens)
        return None
