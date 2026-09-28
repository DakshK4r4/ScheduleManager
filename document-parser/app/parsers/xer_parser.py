from collections import defaultdict
from typing import Dict, List, Optional, Tuple
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

P6_CONSTRAINT_MAP = {
    "CS_MS": "MANDATORY_START",
    "CS_MF": "MANDATORY_FINISH",
    "CS_MSO": "MANDATORY_START",
    "CS_MFO": "MANDATORY_FINISH",
    "CS_SNET": "START_NO_EARLIER",
    "CS_SNLT": "START_NO_LATER",
    "CS_FNET": "FINISH_NO_EARLIER",
    "CS_FNLT": "FINISH_NO_LATER",
    "MANDATORY_START": "MANDATORY_START",
    "MANDATORY_FINISH": "MANDATORY_FINISH",
    "START_NO_EARLIER": "START_NO_EARLIER",
    "START_NO_LATER": "START_NO_LATER",
    "FINISH_NO_EARLIER": "FINISH_NO_EARLIER",
    "FINISH_NO_LATER": "FINISH_NO_LATER",
}


class XerParser(BaseParser):
    def parse(self, content: bytes, filename: str, target_project_code: Optional[str] = None) -> CanonicalSchedule:
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception as e:
            raise ParserError(f"Failed to decode XER file: {str(e)}")

        tables: Dict[str, List[Dict[str, str]]] = {}
        current_table: Optional[str] = None
        fields: Optional[List[str]] = None

        lines = text.splitlines()
        if not lines:
            raise ParserError("Empty XER file")

        # Basic XER header verification
        first_line = lines[0].strip()
        if not (first_line.startswith("ERMHDR") or "%T" in text):
            raise ParserError("Invalid XER format: Missing ERMHDR or table markers")

        for line in lines:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("\t")
            tag = parts[0]

            if tag == "%T":
                if len(parts) > 1:
                    current_table = parts[1].strip()
                    tables[current_table] = []
                    fields = None
            elif tag == "%F":
                fields = [f.strip() for f in parts[1:]]
            elif tag == "%R" and current_table and fields:
                vals = parts[1:]
                # Pad values if shorter than field count
                if len(vals) < len(fields):
                    vals = vals + [""] * (len(fields) - len(vals))
                row_dict = dict(zip(fields, vals[:len(fields)]))
                tables[current_table].append(row_dict)
            elif tag == "%E":
                break

        # 0. Parse Calendars (hours per day)
        calendar_rows = tables.get("CALENDAR", [])
        clndr_id_to_hours_per_day: Dict[str, float] = {}
        clndr_id_to_name: Dict[str, str] = {}
        for c_row in calendar_rows:
            cid = c_row.get("clndr_id", "").strip()
            cname = c_row.get("clndr_name", "").strip()
            day_hr = self.parse_float(c_row.get("day_hr_cnt"))
            if cid:
                if cname:
                    clndr_id_to_name[cid] = cname
                if day_hr and day_hr > 0.0:
                    clndr_id_to_hours_per_day[cid] = day_hr

        # 1. Parse Project (scoped by target_project_code if provided, else best project with tasks)
        project_rows = tables.get("PROJECT", [])
        target_proj_id = ""
        p_row = None

        if project_rows:
            if target_project_code:
                for r in project_rows:
                    if r.get("proj_short_name", "").strip() == target_project_code.strip():
                        p_row = r
                        break
            if not p_row:
                # Count tasks per proj_id in TASK table to pick the real project if multiple exist
                task_counts = defaultdict(int)
                for t in tables.get("TASK", []):
                    t_pid = t.get("proj_id", "").strip()
                    if t_pid:
                        task_counts[t_pid] += 1

                best_p = None
                max_tasks = -1
                for r in project_rows:
                    pid = r.get("proj_id", "").strip()
                    cnt = task_counts.get(pid, 0)
                    if cnt > max_tasks:
                        max_tasks = cnt
                        best_p = r
                p_row = best_p or project_rows[0]

            target_proj_id = p_row.get("proj_id", "").strip()
            proj_code = p_row.get("proj_short_name") or p_row.get("proj_id") or filename.rsplit(".", 1)[0]
            proj_name = p_row.get("proj_name") or p_row.get("project_name") or p_row.get("proj_short_name") or proj_code
            plan_start = self.parse_datetime(p_row.get("plan_start_date") or p_row.get("target_start_date") or p_row.get("start_date"))
            plan_finish = self.parse_datetime(p_row.get("plan_end_date") or p_row.get("target_end_date") or p_row.get("end_date"))
            data_date = self.parse_datetime(p_row.get("last_recalc_date") or p_row.get("scd_end_date") or p_row.get("data_date"))
        else:
            proj_code = filename.rsplit(".", 1)[0]
            proj_name = proj_code
            plan_start, plan_finish, data_date = None, None, None

        canonical_project = CanonicalProject(
            project_code=proj_code.strip(),
            name=proj_name.strip(),
            planned_start=plan_start,
            planned_finish=plan_finish,
            data_date=data_date,
        )

        # 2. Parse WBS (robust scoping and hierarchical code generation)
        wbs_rows = tables.get("PROJWBS", [])
        wbs_proj_ids = {r.get("proj_id", "").strip() for r in wbs_rows if r.get("proj_id", "").strip()}
        filter_wbs_by_proj = bool(target_proj_id and target_proj_id in wbs_proj_ids and len(project_rows) > 1)

        scoped_wbs_rows = []
        for row in wbs_rows:
            row_proj_id = row.get("proj_id", "").strip()
            if filter_wbs_by_proj and row_proj_id != target_proj_id:
                continue
            scoped_wbs_rows.append(row)

        wbs_id_to_short: Dict[str, str] = {}
        wbs_id_to_name: Dict[str, str] = {}
        wbs_id_to_parent_id: Dict[str, Optional[str]] = {}

        for row in scoped_wbs_rows:
            wbs_id = (row.get("wbs_id") or row.get("wbs_code") or row.get("wbs_short_name") or "").strip()
            if not wbs_id:
                continue
            wbs_short = (row.get("wbs_short_name") or row.get("wbs_code") or f"WBS-{wbs_id}").strip()
            wbs_name = (row.get("wbs_name") or row.get("wbs_short_name") or row.get("wbs_code") or wbs_short).strip()
            parent_id = (row.get("parent_wbs_id") or row.get("parent_wbs_code") or "").strip() or None

            wbs_id_to_short[wbs_id] = wbs_short
            wbs_id_to_name[wbs_id] = wbs_name
            wbs_id_to_parent_id[wbs_id] = parent_id

        # Recursive hierarchical code generator to avoid duplicates across branches
        def get_wbs_code(wid: str, visited: Optional[set] = None) -> str:
            if visited is None:
                visited = set()
            if wid in visited:
                return wbs_id_to_short.get(wid, wid)
            visited.add(wid)
            pid = wbs_id_to_parent_id.get(wid)
            short = wbs_id_to_short.get(wid, wid)
            if "." in short:
                return short
            if pid and pid in wbs_id_to_short and pid != wid:
                p_code = get_wbs_code(pid, visited)
                return f"{p_code}.{short}"
            return short

        wbs_id_to_code: Dict[str, str] = {}
        seen_wbs_codes: set = set()
        for wid in wbs_id_to_short:
            code = get_wbs_code(wid)
            if code in seen_wbs_codes:
                code = f"{code}-{wid}"
            seen_wbs_codes.add(code)
            wbs_id_to_code[wid] = code

        canonical_wbs_list: List[CanonicalWBSNode] = []
        for wid in wbs_id_to_short:
            code = wbs_id_to_code[wid]
            wbs_name = wbs_id_to_name.get(wid, code)
            parent_id = wbs_id_to_parent_id.get(wid)
            parent_code = wbs_id_to_code.get(parent_id) if parent_id and parent_id != wid else None

            canonical_wbs_list.append(
                CanonicalWBSNode(
                    code=code,
                    name=wbs_name,
                    parent_code=parent_code,
                    wbs_id=wid,
                )
            )

        # 2b. Parse Activity Codes (ACTVTYPE, ACTVCODE, TASKACTV)
        actv_type_rows = tables.get("ACTVTYPE", [])
        actv_type_id_to_name: Dict[str, str] = {}
        for r in actv_type_rows:
            tid = r.get("actv_code_type_id", "").strip()
            tname = r.get("actv_code_type_name", "").strip() or r.get("actv_code_type_scope", "").strip()
            if tid and tname:
                actv_type_id_to_name[tid] = tname

        actv_code_rows = tables.get("ACTVCODE", [])
        actv_code_id_to_val: Dict[str, Tuple[str, str]] = {}
        for r in actv_code_rows:
            cid = r.get("actv_code_id", "").strip()
            tid = r.get("actv_code_type_id", "").strip()
            val = r.get("actv_code_name", "").strip() or r.get("short_name", "").strip()
            type_name = actv_type_id_to_name.get(tid, "General")
            if cid and val:
                actv_code_id_to_val[cid] = (type_name, val)

        task_id_to_codes: Dict[str, Dict[str, str]] = defaultdict(dict)
        for r in tables.get("TASKACTV", []):
            task_id = r.get("task_id", "").strip()
            code_id = r.get("actv_code_id", "").strip()
            if task_id and code_id in actv_code_id_to_val:
                t_name, val = actv_code_id_to_val[code_id]
                task_id_to_codes[task_id][t_name] = val

        # 2c. Parse Task Memos / Notes
        task_id_to_memos: Dict[str, List[str]] = defaultdict(list)
        for memo_row in tables.get("TASKMEMO", []):
            t_id = memo_row.get("task_id", "").strip()
            memo_txt = memo_row.get("task_memo", "").strip()
            if t_id and memo_txt:
                task_id_to_memos[t_id].append(memo_txt)

        # 3. Parse Activities (scoped by target_proj_id if applicable)
        task_rows = tables.get("TASK", [])
        task_proj_ids = {r.get("proj_id", "").strip() for r in task_rows if r.get("proj_id", "").strip()}
        filter_tasks_by_proj = bool(target_proj_id and target_proj_id in task_proj_ids and len(project_rows) > 1)

        task_id_to_code: Dict[str, str] = {}
        canonical_activities: List[CanonicalActivity] = []

        wbs_code_set = {w.code for w in canonical_wbs_list}
        root_wbs_code = canonical_wbs_list[0].code if canonical_wbs_list else None

        for row in task_rows:
            row_proj_id = row.get("proj_id", "").strip()
            if filter_tasks_by_proj and row_proj_id != target_proj_id:
                continue

            task_id = (row.get("task_id") or "").strip()
            task_code = (row.get("task_code") or row.get("activity_code") or f"ACT-{task_id}").strip()
            if task_id:
                task_id_to_code[task_id] = task_code

            task_name = (row.get("task_name") or row.get("activity_name") or task_code).strip()
            raw_wbs = (row.get("wbs_id") or row.get("wbs_code") or "").strip()

            # Match WBS code
            wbs_code = None
            if raw_wbs in wbs_id_to_code:
                wbs_code = wbs_id_to_code[raw_wbs]
            elif raw_wbs in wbs_code_set:
                wbs_code = raw_wbs
            else:
                wbs_code = root_wbs_code

            # Percent complete
            pct_raw = (
                row.get("phys_percent_comp")
                or row.get("phys_complete_pct")
                or row.get("percent_complete")
                or row.get("pct_complete")
                or row.get("act_work_qty")
            )
            pct = self.parse_float(pct_raw, default=0.0)

            # Status mapping
            raw_status = (row.get("status_code") or row.get("status") or "").strip()
            if raw_status in ("TK_Complete", "COMPLETED", "COMPLETE"):
                status = ActivityStatus.COMPLETED
                pct = 100.0
            elif raw_status in ("TK_Active", "IN_PROGRESS", "PROGRESS", "STARTED"):
                status = ActivityStatus.IN_PROGRESS
                if pct == 0.0:
                    pct = 50.0
            elif raw_status in ("TK_NotStart", "NOT_STARTED", "PLANNED"):
                status = ActivityStatus.NOT_STARTED
                pct = 0.0
            else:
                if pct >= 100.0:
                    status = ActivityStatus.COMPLETED
                    pct = 100.0
                elif pct > 0.0:
                    status = ActivityStatus.IN_PROGRESS
                else:
                    status = ActivityStatus.NOT_STARTED

            # Dates
            act_start = self.parse_datetime(row.get("act_start_date") or row.get("actual_start"))
            act_finish = self.parse_datetime(row.get("act_end_date") or row.get("actual_finish"))
            plan_start = self.parse_datetime(
                row.get("target_start_date")
                or row.get("early_start_date")
                or row.get("start_date")
                or row.get("target_start")
                or row.get("plan_start_date")
            ) or act_start
            plan_finish = self.parse_datetime(
                row.get("target_end_date")
                or row.get("early_end_date")
                or row.get("end_date")
                or row.get("target_end")
                or row.get("plan_end_date")
            ) or act_finish

            # Ensure plan_finish >= plan_start
            if plan_start and plan_finish and plan_finish < plan_start:
                plan_start, plan_finish = plan_finish, plan_start
            elif plan_start and not plan_finish:
                plan_finish = plan_start
            elif plan_finish and not plan_start:
                plan_start = plan_finish

            # Duration: P6 stores hours. Convert using calendar hours-per-day
            cal_id = row.get("clndr_id", "").strip()
            hours_per_day = clndr_id_to_hours_per_day.get(cal_id, 8.0)
            cal_display = clndr_id_to_name.get(cal_id) or cal_id or None

            target_hr = self.parse_float(row.get("target_drtn_hr_cnt") or row.get("duration"))
            remain_hr = self.parse_float(row.get("remain_drtn_hr_cnt"))
            orig_dur = round(target_hr / hours_per_day, 2) if target_hr is not None else None
            if orig_dur is None and plan_start and plan_finish:
                orig_dur = max(0.0, float((plan_finish - plan_start).days))
            rem_dur = round(remain_hr / hours_per_day, 2) if remain_hr is not None else orig_dur

            # Constraints
            raw_cstr_type = row.get("cstr_type", "").strip()
            cstr_type = P6_CONSTRAINT_MAP.get(raw_cstr_type, raw_cstr_type) if raw_cstr_type else None
            cstr_date = self.parse_datetime(row.get("cstr_date"))

            # Activity codes
            act_codes = dict(task_id_to_codes.get(task_id, {}))

            canonical_activities.append(
                CanonicalActivity(
                    activity_code=task_code,
                    name=task_name,
                    wbs_code=wbs_code,
                    activity_type=row.get("task_type") or "TT_Task",
                    status=status,
                    planned_start=plan_start,
                    planned_finish=plan_finish,
                    actual_start=act_start,
                    actual_finish=act_finish,
                    original_duration=orig_dur,
                    remaining_duration=rem_dur,
                    percent_complete=pct,
                    calendar=cal_display,
                    constraint_type=cstr_type,
                    constraint_date=cstr_date,
                    activity_codes=act_codes,
                    notes="\n".join(task_id_to_memos[task_id]) if task_id in task_id_to_memos else None,
                )
            )

        # 4. Parse Relationships (strictly scoped to target_proj_id activities with deduplication)
        pred_rows = tables.get("TASKPRED", [])
        canonical_relationships: List[CanonicalRelationship] = []
        seen_edges: set = set()

        type_map = {
            "PR_FS": RelationshipType.FS,
            "PR_SS": RelationshipType.SS,
            "PR_FF": RelationshipType.FF,
            "PR_SF": RelationshipType.SF,
            "FS": RelationshipType.FS,
            "SS": RelationshipType.SS,
            "FF": RelationshipType.FF,
            "SF": RelationshipType.SF,
        }

        for row in pred_rows:
            task_id = (row.get("task_id") or "").strip()
            pred_id = (row.get("pred_task_id") or "").strip()

            succ_code = task_id_to_code.get(task_id) or (task_id if task_id in task_id_to_code.values() else None)
            pred_code = task_id_to_code.get(pred_id) or (pred_id if pred_id in task_id_to_code.values() else None)

            if not succ_code or not pred_code or succ_code == pred_code:
                continue

            raw_rel = (row.get("pred_type") or "PR_FS").strip()
            rel_type = type_map.get(raw_rel, RelationshipType.FS)

            edge = (pred_code, succ_code, rel_type)
            if edge in seen_edges:
                continue
            seen_edges.add(edge)

            cal_id = row.get("clndr_id", "").strip()
            hours_per_day = clndr_id_to_hours_per_day.get(cal_id, 8.0)
            lag_hr = self.parse_float(row.get("lag_hr_cnt"), default=0.0)
            lag_days = round((lag_hr or 0.0) / hours_per_day, 2)

            canonical_relationships.append(
                CanonicalRelationship(
                    predecessor_code=pred_code,
                    successor_code=succ_code,
                    relationship_type=rel_type,
                    lag=lag_days,
                )
            )

        return CanonicalSchedule(
            project=canonical_project,
            wbs=canonical_wbs_list,
            activities=canonical_activities,
            relationships=canonical_relationships,
        )
