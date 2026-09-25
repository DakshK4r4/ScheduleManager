from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityRelationship, Project
from app.services.calendar_service import CalendarService
from app.services.cpm_engine import CPMEngine


class ScheduleHealthService:
    """
    Deterministic Schedule Health and Quality Assessment Service.
    Implements industry-standard DCMA 14-point schedule assessment guidelines
    with clear, transparent formulas, zero LLM guesswork, and auditable metrics.
    """

    @classmethod
    def evaluate_project_health(cls, db: Session, project_id: str) -> Dict[str, Any]:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"error": f"Project {project_id} not found."}

        activities = db.query(Activity).filter(Activity.project_id == project_id).all()
        relationships = db.query(ActivityRelationship).filter(ActivityRelationship.project_id == project_id).all()

        total_activities = len(activities)
        total_relationships = len(relationships)

        if total_activities == 0:
            return {
                "project_id": project_id,
                "project_code": project.project_code,
                "project_name": project.name,
                "health_score": 0.0,
                "grade": "N/A",
                "summary": "No activities in schedule.",
                "total_activities": 0,
                "total_relationships": 0,
                "metrics": {},
                "recommendations": ["Import or create schedule activities and relationships."],
            }

        # 1. Run deterministic CPM to obtain true topological floats and critical path
        cal_map = CalendarService.load_project_calendars(db, project_id)
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
        act_by_id = {a.id: a for a in activities}
        act_by_code = {a.activity_code: a for a in activities}

        rel_dicts = []
        preds_map: Dict[str, List[str]] = defaultdict(list)
        succs_map: Dict[str, List[str]] = defaultdict(list)

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
                "lag": r.lag,
            })
            if p_code and s_code:
                succs_map[p_code].append(s_code)
                preds_map[s_code].append(p_code)

        cpm_engine = CPMEngine(calendars=cal_map)
        cpm_res = cpm_engine.calculate(
            activities=act_dicts,
            relationships=rel_dicts,
            project_start_date=project.planned_start.date() if project.planned_start else None,
            data_date=project.data_date.date() if project.data_date else None,
        )

        # ---------------------------------------------------------
        # Metric 1: Missing Logic (DCMA Check 1)
        # Target: <= 5% of active activities missing predecessor or successor
        # ---------------------------------------------------------
        open_starts: List[Dict[str, str]] = []
        open_finishes: List[Dict[str, str]] = []
        isolated_acts: List[Dict[str, str]] = []

        for a in activities:
            code = a.activity_code
            has_pred = len(preds_map.get(code, [])) > 0
            has_succ = len(succs_map.get(code, [])) > 0

            if not has_pred and not has_succ:
                isolated_acts.append({"code": code, "name": a.name})
            elif not has_pred:
                open_starts.append({"code": code, "name": a.name})
            elif not has_succ:
                open_finishes.append({"code": code, "name": a.name})

        missing_logic_count = len(open_starts) + len(open_finishes) + len(isolated_acts)
        missing_logic_pct = round((missing_logic_count / total_activities) * 100.0, 1)

        # ---------------------------------------------------------
        # Metric 2: Relationship Leads (Negative Lag) (DCMA Check 2)
        # Target: 0 (leads should be 0)
        # ---------------------------------------------------------
        lead_rels = [
            {"predecessor": r["predecessor_code"], "successor": r["successor_code"], "lag": r["lag"]}
            for r in rel_dicts
            if (r.get("lag") or 0.0) < 0
        ]
        lead_count = len(lead_rels)

        # ---------------------------------------------------------
        # Metric 3: Excessive Positive Lags (DCMA Check 3)
        # Target: <= 5% of relationships have positive lag
        # ---------------------------------------------------------
        lag_rels = [
            {"predecessor": r["predecessor_code"], "successor": r["successor_code"], "lag": r["lag"]}
            for r in rel_dicts
            if (r.get("lag") or 0.0) > 0
        ]
        excessive_lags = [r for r in lag_rels if (r.get("lag") or 0.0) > 5.0]
        lag_pct = round((len(lag_rels) / total_relationships * 100.0), 1) if total_relationships > 0 else 0.0

        # ---------------------------------------------------------
        # Metric 4: Relationship Types (DCMA Check 4)
        # Target: >= 90% Finish-to-Start (FS)
        # ---------------------------------------------------------
        rel_type_counts = {"FS": 0, "SS": 0, "FF": 0, "SF": 0}
        for r in relationships:
            rtype = (r.relationship_type or "FS").upper()
            rel_type_counts[rtype] = rel_type_counts.get(rtype, 0) + 1

        fs_pct = round((rel_type_counts.get("FS", 0) / total_relationships * 100.0), 1) if total_relationships > 0 else 0.0
        sf_count = rel_type_counts.get("SF", 0)

        # ---------------------------------------------------------
        # Metric 5: Hard Constraints (DCMA Check 5)
        # Target: <= 5% hard constraints (MANDATORY_START, MANDATORY_FINISH)
        # ---------------------------------------------------------
        hard_constraints = [
            {"code": a.activity_code, "name": a.name, "type": a.constraint_type, "date": str(a.constraint_date)}
            for a in activities
            if a.constraint_type in ("MANDATORY_START", "MANDATORY_FINISH")
        ]
        soft_constraints = [
            {"code": a.activity_code, "name": a.name, "type": a.constraint_type, "date": str(a.constraint_date)}
            for a in activities
            if a.constraint_type and a.constraint_type not in ("MANDATORY_START", "MANDATORY_FINISH")
        ]
        hard_constraint_pct = round((len(hard_constraints) / total_activities * 100.0), 1)

        # ---------------------------------------------------------
        # Metric 6: High Float / Excessive Slack (DCMA Check 6)
        # Target: <= 5% activities with Total Float > 44 working days
        # ---------------------------------------------------------
        high_float_acts = [
            {"code": code, "total_float": node.total_float}
            for code, node in cpm_res.activities.items()
            if node.total_float is not None and node.total_float > 44.0
        ]
        high_float_pct = round((len(high_float_acts) / total_activities * 100.0), 1)

        # ---------------------------------------------------------
        # Metric 7: Negative Float (DCMA Check 7)
        # Target: 0 (no negative float activities)
        # ---------------------------------------------------------
        negative_float_acts = [
            {"code": code, "total_float": node.total_float}
            for code, node in cpm_res.activities.items()
            if node.total_float is not None and node.total_float < 0.0
        ]
        neg_float_count = len(negative_float_acts)

        # ---------------------------------------------------------
        # Metric 8: Long Durations (DCMA Check 8)
        # Target: <= 5% activities with duration > 44 working days
        # ---------------------------------------------------------
        long_duration_acts = [
            {"code": a.activity_code, "name": a.name, "duration": a.original_duration}
            for a in activities
            if a.original_duration is not None and a.original_duration > 44.0
        ]
        long_dur_pct = round((len(long_duration_acts) / total_activities * 100.0), 1)

        # ---------------------------------------------------------
        # Metric 9: Invalid / Inconsistent Dates (DCMA Check 9)
        # Finish before Start, or Actual Finish without Actual Start
        # ---------------------------------------------------------
        invalid_dates: List[Dict[str, str]] = []
        for a in activities:
            if a.planned_start and a.planned_finish and a.planned_finish < a.planned_start:
                invalid_dates.append({
                    "code": a.activity_code,
                    "issue": f"Planned finish ({a.planned_finish}) precedes planned start ({a.planned_start})",
                })
            if a.actual_finish and not a.actual_start:
                invalid_dates.append({
                    "code": a.activity_code,
                    "issue": "Actual finish recorded without an actual start date",
                })

        # ---------------------------------------------------------
        # Metric 10: Critical Path Integrity (DCMA Check 10)
        # ---------------------------------------------------------
        has_critical_path = len(cpm_res.critical_path) > 0
        critical_count = len(cpm_res.critical_activities)

        # ---------------------------------------------------------
        # Metric 11: Network Disconnection / Sub-Graph Islanding
        # ---------------------------------------------------------
        undirected_adj: Dict[str, Set[str]] = defaultdict(set)
        for r in rel_dicts:
            p_code = r["predecessor_code"]
            s_code = r["successor_code"]
            if p_code and s_code:
                undirected_adj[p_code].add(s_code)
                undirected_adj[s_code].add(p_code)

        visited: Set[str] = set()
        components_count = 0
        for a in activities:
            code = a.activity_code
            if code not in visited:
                components_count += 1
                q = deque([code])
                visited.add(code)
                while q:
                    curr = q.popleft()
                    for neighbor in undirected_adj.get(curr, set()):
                        if neighbor not in visited and neighbor in act_by_code:
                            visited.add(neighbor)
                            q.append(neighbor)

        # ---------------------------------------------------------
        # Deductions & Health Score Computation (Transparent 100-pt scale)
        # ---------------------------------------------------------
        deductions: Dict[str, float] = {}

        # 1. Missing logic: -1.5 points per % of activities missing pred/succ
        d_missing = min(30.0, round(missing_logic_pct * 1.5, 1))
        deductions["missing_logic"] = d_missing

        # 2. Leads (Negative Lags): -5 pts per lead
        d_leads = min(15.0, round(lead_count * 5.0, 1))
        deductions["leads"] = d_leads

        # 3. Excessive Lags (>5d): -1.5 pts per excessive lag
        d_lags = min(10.0, round(len(excessive_lags) * 1.5, 1))
        deductions["excessive_lags"] = d_lags

        # 4. Start-to-Finish relationships: -2 pts per SF link
        d_sf = min(10.0, round(sf_count * 2.0, 1))
        deductions["sf_relationships"] = d_sf

        # 5. Hard constraints: -2 pts per hard constraint
        d_hard_c = min(15.0, round(len(hard_constraints) * 2.0, 1))
        deductions["hard_constraints"] = d_hard_c

        # 6. Negative float: -15 pts if negative float exists
        d_neg_f = 15.0 if neg_float_count > 0 else 0.0
        deductions["negative_float"] = d_neg_f

        # 7. Invalid dates: -5 pts per invalid date
        d_inv_dates = min(10.0, round(len(invalid_dates) * 5.0, 1))
        deductions["invalid_dates"] = d_inv_dates

        total_deduction = sum(deductions.values())
        raw_score = max(0.0, min(100.0, round(100.0 - total_deduction, 1)))

        if raw_score >= 90.0:
            grade = "A"
        elif raw_score >= 80.0:
            grade = "B"
        elif raw_score >= 70.0:
            grade = "C"
        elif raw_score >= 60.0:
            grade = "D"
        else:
            grade = "F"

        # Actionable recommendations
        recommendations: List[str] = []
        if open_starts:
            recommendations.append(f"Connect predecessor logic for {len(open_starts)} open-start activities (e.g. {open_starts[0]['code']}).")
        if open_finishes:
            recommendations.append(f"Connect successor logic for {len(open_finishes)} open-finish activities (e.g. {open_finishes[0]['code']}).")
        if lead_count > 0:
            recommendations.append(f"Remove {lead_count} negative lag (lead) relationships; convert to Start-to-Start (SS) with positive lag.")
        if neg_float_count > 0:
            recommendations.append(f"Resolve negative float on {neg_float_count} activities by mitigating delays or adjusting hard finish constraints.")
        if hard_constraints:
            recommendations.append(f"Review {len(hard_constraints)} hard constraints (MANDATORY_START/FINISH); consider soft constraints to allow CPM float.")
        if not recommendations:
            recommendations.append("Schedule demonstrates robust CPM network integrity conforming to DCMA standards.")

        return {
            "project_id": project_id,
            "project_code": project.project_code,
            "project_name": project.name,
            "health_score": raw_score,
            "grade": grade,
            "total_activities": total_activities,
            "total_relationships": total_relationships,
            "critical_activities_count": critical_count,
            "critical_path_length_days": cpm_res.project_duration_days,
            "has_continuous_critical_path": has_critical_path,
            "disconnected_components_count": components_count,
            "deductions": deductions,
            "metrics": {
                "missing_logic": {
                    "open_starts_count": len(open_starts),
                    "open_starts": open_starts[:10],
                    "open_finishes_count": len(open_finishes),
                    "open_finishes": open_finishes[:10],
                    "isolated_count": len(isolated_acts),
                    "total_missing_logic": missing_logic_count,
                    "percentage": missing_logic_pct,
                    "status": "PASS" if missing_logic_pct <= 5.0 else ("WARNING" if missing_logic_pct <= 10.0 else "FAIL"),
                },
                "leads": {
                    "count": lead_count,
                    "leads": lead_rels,
                    "status": "PASS" if lead_count == 0 else "FAIL",
                },
                "lags": {
                    "count": len(lag_rels),
                    "excessive_lags_count": len(excessive_lags),
                    "excessive_lags": excessive_lags[:10],
                    "percentage": lag_pct,
                    "status": "PASS" if len(excessive_lags) == 0 else "WARNING",
                },
                "relationship_types": {
                    "distribution": rel_type_counts,
                    "fs_percentage": fs_pct,
                    "sf_count": sf_count,
                    "status": "PASS" if fs_pct >= 90.0 and sf_count == 0 else "WARNING",
                },
                "hard_constraints": {
                    "count": len(hard_constraints),
                    "percentage": hard_constraint_pct,
                    "activities": hard_constraints,
                    "soft_constraints_count": len(soft_constraints),
                    "status": "PASS" if hard_constraint_pct <= 5.0 else "FAIL",
                },
                "negative_float": {
                    "count": neg_float_count,
                    "activities": negative_float_acts[:10],
                    "status": "PASS" if neg_float_count == 0 else "FAIL",
                },
                "high_float": {
                    "count": len(high_float_acts),
                    "percentage": high_float_pct,
                    "activities": high_float_acts[:10],
                    "status": "PASS" if high_float_pct <= 5.0 else "WARNING",
                },
                "long_durations": {
                    "count": len(long_duration_acts),
                    "percentage": long_dur_pct,
                    "activities": long_duration_acts[:10],
                    "status": "PASS" if long_dur_pct <= 5.0 else "WARNING",
                },
                "invalid_dates": {
                    "count": len(invalid_dates),
                    "issues": invalid_dates,
                    "status": "PASS" if len(invalid_dates) == 0 else "FAIL",
                },
                "network_connectivity": {
                    "components_count": components_count,
                    "status": "PASS" if components_count <= 1 else "WARNING",
                },
            },
            "recommendations": recommendations,
        }
