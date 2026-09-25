from __future__ import annotations

import logging
import math
import random
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityRelationship, Project
from app.repositories.activity_repo import ActivityRepository
from app.repositories.relationship_repo import RelationshipRepository
from app.services.calendar_service import CalendarService
from app.services.cpm_engine import CPMEngine, CPMResult
from app.services.historical_analytics_service import HistoricalAnalyticsService

logger = logging.getLogger(__name__)

MIN_HISTORICAL_SAMPLES = 3


class MonteCarloSimulationService:
    """
    Deterministic Monte Carlo Schedule Risk Simulation Service.
    Samples activity durations using empirical historical duration ratios from
    Institutional Memory, runs repeated CPM forward/backward passes, and computes
    P50, P80, P90 completion dates, probability of meeting target finish,
    and activity criticality indices. GUARANTEES zero database mutation.
    """

    @classmethod
    def run_simulation(
        cls,
        db: Session,
        project_id: str,
        iterations: int = 100,
        seed: Optional[int] = 42,
        target_finish_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"error": f"Project '{project_id}' not found."}

        activities, _ = ActivityRepository.filter_activities(
            db=db, project_id=project_id, page=1, page_size=10000
        )
        relationships = RelationshipRepository.get_by_project(db, project_id)

        if not activities:
            return {"error": "Project contains no activities for simulation."}

        # 1. Fetch empirical historical duration ratios from Institutional Memory
        # Gathers completed activities across the database to derive realistic performance variance
        hist_durations = HistoricalAnalyticsService.calculate_durations(db=db, project_id=project_id)
        discipline_ratios: Dict[str, List[float]] = defaultdict(list)
        all_ratios: List[float] = []

        for item in hist_durations.items:
            if item.planned_duration_days > 0 and item.actual_duration_days > 0:
                ratio = round(item.actual_duration_days / item.planned_duration_days, 3)
                disc = (item.discipline or "DEFAULT").upper()
                discipline_ratios[disc].append(ratio)
                all_ratios.append(ratio)

        has_sufficient_data = len(all_ratios) >= MIN_HISTORICAL_SAMPLES
        data_status = "EMPIRICAL" if has_sufficient_data else "PARAMETRIC_FALLBACK"

        # 2. Build base activity representations
        act_by_id = {a.id: a for a in activities}
        act_dicts_base: List[Dict[str, Any]] = []
        for a in activities:
            dur = a.original_duration or 0.0
            act_dicts_base.append({
                "id": a.id,
                "activity_code": a.activity_code,
                "name": a.name,
                "discipline": (a.discipline or "").upper(),
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

        # 3. Build relationships representation with safe activity code mapping
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

        # 4. Relational calendars and CPM engine
        cal_map = CalendarService.load_project_calendars(db, project_id)
        engine = CPMEngine(calendars=cal_map)

        proj_start = project.planned_start.date() if project.planned_start else None
        data_date = project.data_date.date() if project.data_date else None

        base_cpm = engine.calculate(
            activities=act_dicts_base,
            relationships=rel_dicts,
            project_start_date=proj_start,
            data_date=data_date,
        )

        base_finish = base_cpm.project_finish
        effective_target = target_finish_date or (project.planned_finish.date() if project.planned_finish else base_finish)

        # 5. Run Monte Carlo Iterations
        if seed is not None:
            rng = random.Random(seed)
        else:
            rng = random.Random()

        simulated_finish_dates: List[date] = []
        simulated_duration_days: List[float] = []
        critical_counts: Dict[str, int] = defaultdict(int)

        for _ in range(iterations):
            iter_acts = deepcopy(act_dicts_base)
            for act in iter_acts:
                # Do not re-sample finished activities
                if act.get("status") == "COMPLETED":
                    continue

                orig_dur = float(act.get("remaining_duration") or act.get("original_duration") or 1.0)
                disc = act.get("discipline") or "DEFAULT"
                known_ratios = discipline_ratios.get(disc) or all_ratios

                if has_sufficient_data and known_ratios:
                    # Sample duration factor using triangular distribution based on observed ratios
                    min_r = max(0.8, min(known_ratios) * 0.9)
                    mode_r = sum(known_ratios) / len(known_ratios)
                    max_r = max(1.2, max(known_ratios) * 1.1)
                    factor = rng.triangular(min_r, max_r, mode_r)
                else:
                    # Conservative industry standard triangular distribution (0.9, 1.35, 1.05)
                    factor = rng.triangular(0.9, 1.35, 1.05)

                sampled_dur = max(1.0, round(orig_dur * factor, 1))
                act["original_duration"] = sampled_dur
                if act.get("remaining_duration") is not None:
                    act["remaining_duration"] = sampled_dur

            iter_cpm = engine.calculate(
                activities=iter_acts,
                relationships=rel_dicts,
                project_start_date=proj_start,
                data_date=data_date,
            )

            if iter_cpm.project_finish:
                simulated_finish_dates.append(iter_cpm.project_finish)
                if proj_start:
                    simulated_duration_days.append((iter_cpm.project_finish - proj_start).days)

            for c_code in iter_cpm.critical_activities:
                critical_counts[c_code] += 1

        if not simulated_finish_dates:
            return {"error": "Simulation could not produce valid finish dates."}

        # 6. Calculate Percentiles and Confidence Metrics
        simulated_finish_dates.sort()
        simulated_duration_days.sort()

        def percentile(lst: list, p: float):
            idx = int(round(p * (len(lst) - 1)))
            return lst[idx]

        p50_finish = percentile(simulated_finish_dates, 0.50)
        p80_finish = percentile(simulated_finish_dates, 0.80)
        p90_finish = percentile(simulated_finish_dates, 0.90)

        p50_dur = percentile(simulated_duration_days, 0.50) if simulated_duration_days else 0
        p80_dur = percentile(simulated_duration_days, 0.80) if simulated_duration_days else 0

        # Probability of meeting target finish
        on_time_count = sum(1 for d in simulated_finish_dates if effective_target and d <= effective_target)
        on_time_prob = round((on_time_count / len(simulated_finish_dates)) * 100.0, 1)

        # Criticality indices
        criticality_index = {
            code: round((count / iterations) * 100.0, 1)
            for code, count in sorted(critical_counts.items(), key=lambda x: x[1], reverse=True)
        }

        return {
            "project_id": project_id,
            "project_code": project.project_code,
            "iterations": iterations,
            "seed": seed,
            "data_status": data_status,
            "historical_sample_size": len(all_ratios),
            "base_planned_finish": base_finish.isoformat() if base_finish else None,
            "target_finish_date": effective_target.isoformat() if effective_target else None,
            "p50_finish": p50_finish.isoformat(),
            "p80_finish": p80_finish.isoformat(),
            "p90_finish": p90_finish.isoformat(),
            "p50_duration_days": p50_dur,
            "p80_duration_days": p80_dur,
            "probability_meeting_target_percent": on_time_prob,
            "criticality_index": criticality_index,
            "read_only_guarantee": True,
            "explanation": (
                f"Simulated {iterations} iterations with random seed {seed}. "
                f"P50 completion is projected on {p50_finish.isoformat()}, "
                f"P80 confidence on {p80_finish.isoformat()} ({on_time_prob}% on-time probability "
                f"against target {effective_target.isoformat() if effective_target else 'N/A'})."
            ),
        }
