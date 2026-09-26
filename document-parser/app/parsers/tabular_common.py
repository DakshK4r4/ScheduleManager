from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple
from app.models.canonical import (
    ActivityStatus,
    CanonicalActivity,
    CanonicalProject,
    CanonicalRelationship,
    CanonicalSchedule,
    CanonicalWBSNode,
    RelationshipType,
)
from app.parsers.base import BaseParser, ParserError


COLUMN_ALIASES: Dict[str, List[str]] = {
    "activity_code": [
        "activity id", "activity code", "task id", "task code",
        "activity_id", "activity_code", "act id", "act_id", "code", "id", "activity"
    ],
    "name": [
        "activity name", "task name", "activity description", "description",
        "task description", "name", "task_name", "activity_name"
    ],
    "wbs_code": [
        "wbs", "wbs code", "wbs id", "wbs name", "wbs_code",
        "work breakdown structure", "discipline", "area", "phase"
    ],
    "status": [
        "status", "activity status", "task status", "status code", "status_code"
    ],
    "planned_start": [
        "start", "planned start", "target start", "early start",
        "start date", "planned_start", "target_start_date", "start_date"
    ],
    "planned_finish": [
        "finish", "planned finish", "target finish", "early finish",
        "finish date", "end date", "planned_finish", "target_end_date", "finish_date", "end"
    ],
    "actual_start": [
        "actual start", "actual start date", "act start", "actual_start"
    ],
    "actual_finish": [
        "actual finish", "actual finish date", "act finish", "actual_finish"
    ],
    "original_duration": [
        "duration", "original duration", "planned duration", "dur", "days",
        "original_duration", "target_drtn_hr_cnt", "planned_duration"
    ],
    "remaining_duration": [
        "remaining duration", "rem duration", "remaining", "remaining_duration"
    ],
    "percent_complete": [
        "% complete", "percent complete", "percentage complete", "% comp",
        "pct complete", "percent_complete", "progress", "%", "phys_percent_comp"
    ],
    "calendar": [
        "calendar", "calendar name", "calendar id", "clndr"
    ],
    "predecessors": [
        "predecessor", "predecessors", "pred", "preds", "predecessor code"
    ],
    "successors": [
        "successor", "successors", "succ", "succs", "successor code"
    ],
}


def map_columns(headers: List[str]) -> Dict[str, str]:
    """
    Map raw table headers to canonical column keys using alias matching.
    Returns: dict mapping canonical_key -> original_header
    """
    normalized_to_original = {h.strip().lower(): h for h in headers if h}
    matched: Dict[str, str] = {}

    for canonical_key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized_to_original:
                matched[canonical_key] = normalized_to_original[alias]
                break

    # Required field verification
    missing = []
    if "name" not in matched:
        missing.append("Activity Name / Description")

    if missing:
        found_cols = ", ".join(f"'{h}'" for h in headers)
        raise ParserError(
            f"Unable to map required schedule columns from table headers. "
            f"Missing required columns: {', '.join(missing)}. "
            f"Columns found in file: [{found_cols}]. "
            f"Supported aliases for Name include: {', '.join(COLUMN_ALIASES['name'][:5])}."
        )

    # If activity_code is not present in headers, permit deterministic auto-generation
    if "activity_code" not in matched:
        matched["activity_code"] = "__AUTO_GENERATED__"

    return matched


REL_PATTERN = re.compile(
    r"^([A-Za-z0-9_\-\.]+?)(?:[\s\-_]*)(?:\(?\[?\s*(FS|SS|FF|SF)?\s*([+\-]?\d+(?:\.\d+)?(?:d|h|w)?)?\s*\)?\]?)?$",
    re.IGNORECASE,
)


def parse_relationship_token(token: str, current_code: str, is_predecessor: bool = True) -> Optional[CanonicalRelationship]:
    token = token.strip()
    if not token:
        return None

    match = REL_PATTERN.match(token)
    if not match or not match.group(1):
        target_code = token.strip()
        rel_type = RelationshipType.FS
        lag = 0.0
    else:
        target_code = match.group(1).strip()
        raw_type = (match.group(2) or "FS").upper()
        rel_type = RelationshipType[raw_type] if raw_type in RelationshipType.__members__ else RelationshipType.FS
        raw_lag = match.group(3)
        lag = 0.0
        if raw_lag:
            clean_lag = raw_lag.lower().replace("d", "").replace("h", "").replace("w", "")
            try:
                lag = float(clean_lag)
                if "h" in raw_lag.lower():
                    lag = round(lag / 8.0, 2)
                elif "w" in raw_lag.lower():
                    lag = round(lag * 5.0, 2)
            except ValueError:
                lag = 0.0

    if is_predecessor:
        return CanonicalRelationship(
            predecessor_code=target_code,
            successor_code=current_code,
            relationship_type=rel_type,
            lag=lag,
        )
    else:
        return CanonicalRelationship(
            predecessor_code=current_code,
            successor_code=target_code,
            relationship_type=rel_type,
            lag=lag,
        )


def generate_stable_activity_code(
    name: str,
    wbs_code: Optional[str] = None,
    seen_codes: Optional[Dict[str, int]] = None,
) -> str:
    """
    Generate a stable, deterministic activity code from task name and WBS.
    Preserves identity continuity across repeated imports, reordered rows, and inserted rows.
    """
    import hashlib
    slug = re.sub(r"[^A-Za-z0-9]+", "", name).upper()[:8]
    if not slug:
        slug = "TASK"
    seed = f"{wbs_code or 'GEN'}:{name.strip().lower()}"
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6].upper()
    base_code = f"ACT-{slug}-{h}"

    if seen_codes is not None:
        count = seen_codes.get(base_code, 0)
        seen_codes[base_code] = count + 1
        if count > 0:
            return f"{base_code}-{count+1}"
    return base_code


def build_canonical_schedule_from_rows(
    rows: List[Dict[str, str]],
    col_map: Dict[str, str],
    filename: str,
    parser: BaseParser,
) -> CanonicalSchedule:
    proj_code = filename.rsplit(".", 1)[0]
    canonical_activities: List[CanonicalActivity] = []
    canonical_wbs_map: Dict[str, CanonicalWBSNode] = {}
    canonical_relationships: List[CanonicalRelationship] = []
    seen_activities: Set[str] = set()
    generated_code_counts: Dict[str, int] = {}

    for idx, row in enumerate(rows, start=2):  # row 1 is header
        raw_name = row.get(col_map.get("name", ""), "").strip() or f"Task-{idx-1}"
        raw_wbs = row.get(col_map.get("wbs_code", ""), "").strip() if "wbs_code" in col_map else None

        if col_map.get("activity_code") == "__AUTO_GENERATED__":
            raw_code = generate_stable_activity_code(raw_name, raw_wbs, generated_code_counts)
        else:
            raw_code = row.get(col_map.get("activity_code", ""), "").strip()
            if not raw_code:
                raw_code = generate_stable_activity_code(raw_name, raw_wbs, generated_code_counts)

        if raw_wbs and raw_wbs not in canonical_wbs_map:
            canonical_wbs_map[raw_wbs] = CanonicalWBSNode(
                code=raw_wbs,
                name=raw_wbs,
                parent_code=None,
            )

        # Status
        status_val = (row.get(col_map.get("status", ""), "") or "").lower().strip()
        if "not" in status_val or "unstarted" in status_val:
            status = ActivityStatus.NOT_STARTED
            default_pct = 0.0
        elif "complete" in status_val or status_val in ("100", "done"):
            status = ActivityStatus.COMPLETED
            default_pct = 100.0
        elif "progress" in status_val or "active" in status_val or "started" in status_val:
            status = ActivityStatus.IN_PROGRESS
            default_pct = 50.0
        else:
            status = ActivityStatus.NOT_STARTED
            default_pct = 0.0

        pct_val = parser.parse_float(row.get(col_map.get("percent_complete", ""), ""))
        pct = pct_val if pct_val is not None else default_pct

        # Dates
        plan_start = parser.parse_datetime(row.get(col_map.get("planned_start", ""), ""))
        plan_finish = parser.parse_datetime(row.get(col_map.get("planned_finish", ""), ""))
        act_start = parser.parse_datetime(row.get(col_map.get("actual_start", ""), ""))
        act_finish = parser.parse_datetime(row.get(col_map.get("actual_finish", ""), ""))

        # Duration
        orig_dur = parser.parse_float(row.get(col_map.get("original_duration", ""), ""))
        rem_dur = parser.parse_float(row.get(col_map.get("remaining_duration", ""), ""))
        if rem_dur is None and orig_dur is not None:
            rem_dur = 0.0 if status == ActivityStatus.COMPLETED else orig_dur * (1.0 - (pct / 100.0))

        canonical_activities.append(
            CanonicalActivity(
                activity_code=raw_code,
                name=raw_name,
                wbs_code=raw_wbs if raw_wbs else None,
                activity_type="TT_Task",
                status=status,
                planned_start=plan_start,
                planned_finish=plan_finish,
                actual_start=act_start,
                actual_finish=act_finish,
                original_duration=round(orig_dur, 2) if orig_dur is not None else None,
                remaining_duration=round(rem_dur, 2) if rem_dur is not None else None,
                percent_complete=pct,
                calendar=row.get(col_map.get("calendar", ""), "").strip() or None,
            )
        )
        seen_activities.add(raw_code)

        # Predecessors / Successors in table
        if "predecessors" in col_map:
            preds_raw = row.get(col_map["predecessors"], "")
            if preds_raw:
                for token in re.split(r"[,;]\s*", str(preds_raw)):
                    rel = parse_relationship_token(token, current_code=raw_code, is_predecessor=True)
                    if rel:
                        canonical_relationships.append(rel)

        if "successors" in col_map:
            succs_raw = row.get(col_map["successors"], "")
            if succs_raw:
                for token in re.split(r"[,;]\s*", str(succs_raw)):
                    rel = parse_relationship_token(token, current_code=raw_code, is_predecessor=False)
                    if rel:
                        canonical_relationships.append(rel)

    # Determine Project dates from activities
    starts = [a.planned_start for a in canonical_activities if a.planned_start]
    finishes = [a.planned_finish for a in canonical_activities if a.planned_finish]
    proj_start = min(starts) if starts else None
    proj_finish = max(finishes) if finishes else None

    canonical_project = CanonicalProject(
        project_code=proj_code,
        name=proj_code,
        planned_start=proj_start,
        planned_finish=proj_finish,
        data_date=proj_start,
    )

    return CanonicalSchedule(
        project=canonical_project,
        wbs=list(canonical_wbs_map.values()),
        activities=canonical_activities,
        relationships=canonical_relationships,
    )
