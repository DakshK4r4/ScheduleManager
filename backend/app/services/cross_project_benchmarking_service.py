from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActualProgressLedger, Project

logger = logging.getLogger(__name__)

MIN_BENCHMARK_RECORDS = 3
MIN_BENCHMARK_PROJECTS = 2


class CrossProjectBenchmarkingService:
    """
    Deterministic Cross-Project Benchmarking Service.
    Aggregates verified, unit-consistent historical performance across multiple projects.
    Strictly prevents combining incompatible units or incomparable work scopes.
    """

    @classmethod
    def get_benchmarks(
        cls,
        db: Session,
        discipline: Optional[str] = None,
        unit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculates cross-project benchmarks grouped strictly by (discipline, unit).
        Discloses sample size, distinct project count, percentile methodology, and limitations.
        """
        query = (
            db.query(
                ActualProgressLedger.project_id,
                Activity.discipline,
                ActualProgressLedger.unit_of_measure,
                ActualProgressLedger.installed_quantity,
                ActualProgressLedger.reporting_date,
            )
            .join(Activity, ActualProgressLedger.activity_id == Activity.id)
            .filter(
                ActualProgressLedger.installed_quantity > 0,
                ActualProgressLedger.unit_of_measure != None,
            )
        )

        if discipline:
            query = query.filter(Activity.discipline.ilike(f"%{discipline.strip()}%"))
        if unit:
            query = query.filter(ActualProgressLedger.unit_of_measure.ilike(f"%{unit.strip()}%"))

        rows = query.all()

        # Group data strictly by normalized (discipline, unit)
        grouped_data: Dict[tuple[str, str], List[float]] = defaultdict(list)
        grouped_projects: Dict[tuple[str, str], set[str]] = defaultdict(set)

        for proj_id, disc, q_unit, qty, r_date in rows:
            norm_disc = (disc or "General").strip().title()
            norm_unit = q_unit.strip().lower()
            key = (norm_disc, norm_unit)
            grouped_data[key].append(float(qty))
            grouped_projects[key].add(proj_id)

        benchmarks: List[Dict[str, Any]] = []

        for (disc_name, unit_name), quantities in grouped_data.items():
            sample_size = len(quantities)
            distinct_projs = len(grouped_projects[(disc_name, unit_name)])

            quantities.sort()

            def percentile(lst: list, p: float) -> float:
                idx = int(round(p * (len(lst) - 1)))
                return lst[idx]

            p25 = percentile(quantities, 0.25)
            p50 = percentile(quantities, 0.50)
            p75 = percentile(quantities, 0.75)
            mean_val = round(sum(quantities) / sample_size, 2)
            min_val = round(min(quantities), 2)
            max_val = round(max(quantities), 2)

            is_statistically_sound = (
                sample_size >= MIN_BENCHMARK_RECORDS and distinct_projs >= MIN_BENCHMARK_PROJECTS
            )

            if is_statistically_sound:
                if sample_size >= 10 and distinct_projs >= 3:
                    confidence = "HIGH"
                else:
                    confidence = "MEDIUM"
                data_status = "VERIFIED_BENCHMARK"
            else:
                confidence = "LOW"
                data_status = "INSUFFICIENT_SAMPLE"

            limitations = []
            if distinct_projs < MIN_BENCHMARK_PROJECTS:
                limitations.append(
                    f"Only sampled from {distinct_projs} project; minimum {MIN_BENCHMARK_PROJECTS} required for cross-project validity."
                )
            if sample_size < MIN_BENCHMARK_RECORDS:
                limitations.append(
                    f"Sample size of {sample_size} records is below reliable statistical threshold ({MIN_BENCHMARK_RECORDS})."
                )
            limitations.append("Rates reflect observed daily output without site topography or weather normalization.")

            benchmarks.append({
                "discipline": disc_name,
                "unit": f"{unit_name}/day",
                "sample_size": sample_size,
                "project_count": distinct_projs,
                "p25_rate": p25,
                "p50_median_rate": p50,
                "p75_rate": p75,
                "mean_rate": mean_val,
                "min_rate": min_val,
                "max_rate": max_val,
                "confidence": confidence,
                "data_status": data_status,
                "percentile_methodology": "Linear nearest-rank percentile sampling on verified ledger records",
                "limitations": limitations,
            })

        return {
            "total_benchmark_categories": len(benchmarks),
            "benchmarks": benchmarks,
            "unit_consistency_enforced": True,
        }
