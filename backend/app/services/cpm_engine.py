from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.services.calendar_service import CalendarService, CalendarSpec

logger = logging.getLogger("cpm_engine")


class CPMException(Exception):
    """Raised when CPM calculation cannot proceed, e.g. on circular dependencies."""
    pass


class CycleDetectedException(CPMException):
    def __init__(self, cycle_nodes: List[str], message: Optional[str] = None):
        msg = message or f"CPM cycle detected involving activities: {', '.join(cycle_nodes)}"
        super().__init__(msg)
        self.cycle_nodes = cycle_nodes


@dataclass
class CPMActivityNode:
    id: str
    activity_code: str
    name: str
    duration: float  # In working days
    planned_start: Optional[date] = None
    planned_finish: Optional[date] = None
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    calendar_id: Optional[str] = None
    constraint_type: Optional[str] = None  # e.g., 'MANDATORY_START', 'MANDATORY_FINISH', 'START_NO_EARLIER', 'FINISH_NO_LATER'
    constraint_date: Optional[date] = None
    status: str = "NOT_STARTED"            # NOT_STARTED, IN_PROGRESS, COMPLETED
    percent_complete: float = 0.0
    remaining_duration: Optional[float] = None

    # Calculated CPM fields
    early_start: Optional[date] = None
    early_finish: Optional[date] = None
    late_start: Optional[date] = None
    late_finish: Optional[date] = None
    forecast_start: Optional[date] = None
    forecast_finish: Optional[date] = None
    finish_variance: Optional[float] = None  # in days: forecast/actual finish - planned finish
    total_float: Optional[float] = None  # in working days
    free_float: Optional[float] = None   # in working days
    is_critical: bool = False
    is_near_critical: bool = False
    has_negative_float: bool = False
    is_open_start: bool = False
    is_open_finish: bool = False
    float_warning: Optional[str] = None      # e.g., 'HIGH_FLOAT', 'OPEN_FINISH', 'DISCONNECTED', 'NEGATIVE_FLOAT'
    float_explanation: Optional[str] = None  # Human-readable explanation of why float is large or negative
    driving_predecessor_id: Optional[str] = None
    driving_predecessor_code: Optional[str] = None
    float_path_index: Optional[int] = None


@dataclass
class CPMRelationshipEdge:
    id: str
    predecessor_id: str
    successor_id: str
    predecessor_code: str
    successor_code: str
    relationship_type: str = "FS"  # FS, SS, FF, SF
    lag: float = 0.0               # in working days
    is_driving: bool = False
    relationship_float: Optional[float] = None


@dataclass
class CPMResult:
    project_start: Optional[date]
    project_finish: Optional[date]
    project_duration_days: float
    activities: Dict[str, CPMActivityNode]  # keyed by activity_id
    relationships: List[CPMRelationshipEdge]
    critical_path: List[str]               # activity_codes along the longest/critical path
    critical_activities: List[str]          # all activity_codes with total_float <= 0
    near_critical_activities: List[str]     # activity_codes with 0 < total_float <= near_critical_threshold
    negative_float_activities: List[str]    # activity_codes with total_float < 0
    float_paths: List[List[str]]            # distinct paths categorized by float tier
    isolated_activities: List[str]          # activities with no predecessors AND no successors
    open_start_activities: List[str]        # activities with no predecessors
    open_finish_activities: List[str]       # activities with no successors
    high_float_activities: List[str] = field(default_factory=list)
    data_date: Optional[date] = None
    must_finish_by_date: Optional[date] = None
    near_critical_threshold: float = 5.0
    cycles_detected: bool = False
    error: Optional[str] = None


class CPMEngine:
    """
    Deterministic Critical Path Method (CPM) Engine.
    Executes in O(V + E) time without LLM calls or database side-effects.
    """

    def __init__(
        self,
        calendars: Optional[Dict[str, CalendarSpec]] = None,
        default_calendar: Optional[CalendarSpec] = None,
        near_critical_threshold: float = 5.0,
        high_float_threshold: float = 40.0,
    ):
        self.calendars = calendars or {}
        self.default_calendar = default_calendar or CalendarService.DEFAULT_CALENDAR
        self.near_critical_threshold = near_critical_threshold
        self.high_float_threshold = high_float_threshold

    def get_calendar(self, calendar_id: Optional[str]) -> CalendarSpec:
        if calendar_id and calendar_id in self.calendars:
            return self.calendars[calendar_id]
        return self.default_calendar

    # -------------------------------------------------------------------------
    # 1. GRAPH VALIDATION & CYCLE DETECTION (Kahn's & Tarjan's SCC)
    # -------------------------------------------------------------------------

    @classmethod
    def detect_cycles(
        cls,
        nodes: Dict[str, CPMActivityNode],
        edges: List[CPMRelationshipEdge],
    ) -> List[str]:
        """
        Detects cycles using Tarjan's Strongly Connected Components algorithm.
        Returns the list of activity_codes involved in any detected cycle.
        """
        adj: Dict[str, List[str]] = defaultdict(list)
        for e in edges:
            if e.predecessor_id in nodes and e.successor_id in nodes:
                adj[e.predecessor_id].append(e.successor_id)

        index = 0
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on_stack: Set[str] = set()
        stack: List[str] = []
        cycle_node_ids: Set[str] = set()

        def strongconnect(node_id: str):
            nonlocal index
            indices[node_id] = index
            lowlink[node_id] = index
            index += 1
            stack.append(node_id)
            on_stack.add(node_id)

            for succ_id in adj.get(node_id, []):
                if succ_id not in indices:
                    strongconnect(succ_id)
                    lowlink[node_id] = min(lowlink[node_id], lowlink[succ_id])
                elif succ_id in on_stack:
                    lowlink[node_id] = min(lowlink[node_id], indices[succ_id])

            # If node_id is a root node of an SCC
            if lowlink[node_id] == indices[node_id]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    scc.append(w)
                    if w == node_id:
                        break
                # An SCC is a cycle if it contains more than 1 node, or a self-loop
                if len(scc) > 1 or (len(scc) == 1 and scc[0] in adj.get(scc[0], [])):
                    cycle_node_ids.update(scc)

        for n_id in nodes.keys():
            if n_id not in indices:
                strongconnect(n_id)

        return [nodes[nid].activity_code for nid in cycle_node_ids if nid in nodes]

    @classmethod
    def topological_sort(
        cls,
        nodes: Dict[str, CPMActivityNode],
        edges: List[CPMRelationshipEdge],
    ) -> List[str]:
        """
        Performs Kahn's algorithm topological sort.
        Returns ordered list of node IDs.
        Raises CycleDetectedException if a cycle is present.
        """
        in_degree: Dict[str, int] = {n_id: 0 for n_id in nodes}
        adj: Dict[str, List[str]] = defaultdict(list)

        for e in edges:
            if e.predecessor_id in nodes and e.successor_id in nodes:
                adj[e.predecessor_id].append(e.successor_id)
                in_degree[e.successor_id] += 1

        queue = deque([n_id for n_id, deg in in_degree.items() if deg == 0])
        ordered: List[str] = []

        while queue:
            curr = queue.popleft()
            ordered.append(curr)
            for succ in adj.get(curr, []):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if len(ordered) < len(nodes):
            cycle_nodes = cls.detect_cycles(nodes, edges)
            raise CycleDetectedException(cycle_nodes)

        return ordered

    # -------------------------------------------------------------------------
    # 2. DETERMINISTIC FORWARD PASS
    # -------------------------------------------------------------------------

    def _forward_pass(
        self,
        nodes: Dict[str, CPMActivityNode],
        edges: List[CPMRelationshipEdge],
        ordered_ids: List[str],
        project_start_date: date,
        data_date: Optional[date] = None,
    ):
        """
        Calculates Early Start (ES) and Early Finish (EF) for each activity.
        Respects FS, SS, FF, SF relationships, lags, calendars, and constraints.
        Identifies driving predecessors for each activity.
        Accounts for data_date cutoff and status-aware progress.
        """
        # Map incoming edges by successor_id
        incoming_edges: Dict[str, List[CPMRelationshipEdge]] = defaultdict(list)
        for e in edges:
            incoming_edges[e.successor_id].append(e)

        for act_id in ordered_ids:
            act = nodes[act_id]
            cal = self.get_calendar(act.calendar_id)
            duration = max(0.0, act.duration)

            candidate_early_starts: List[Tuple[date, CPMRelationshipEdge]] = []

            for edge in incoming_edges.get(act_id, []):
                pred = nodes.get(edge.predecessor_id)
                if not pred or pred.early_start is None or pred.early_finish is None:
                    continue

                rel_type = (edge.relationship_type or "FS").upper()
                lag = edge.lag or 0.0

                if rel_type == "FS":
                    # Predecessor must finish before successor starts
                    standard_start = CalendarService.next_working_day(pred.early_finish + timedelta(days=1), cal)
                    if lag > 0:
                        cur = standard_start
                        for _ in range(int(round(lag))):
                            cur = CalendarService.next_working_day(cur + timedelta(days=1), cal)
                        es_candidate = cur
                    elif lag < 0:
                        cur = standard_start
                        for _ in range(int(round(abs(lag)))):
                            cur = CalendarService.prev_working_day(cur - timedelta(days=1), cal)
                        es_candidate = cur
                    else:
                        es_candidate = standard_start

                elif rel_type == "SS":
                    # Predecessor starts, and with lag, successor can start
                    base_start = pred.early_start
                    if lag > 0:
                        cur = base_start
                        for _ in range(int(round(lag))):
                            cur = CalendarService.next_working_day(cur + timedelta(days=1), cal)
                        es_candidate = cur
                    elif lag < 0:
                        cur = base_start
                        for _ in range(int(round(abs(lag)))):
                            cur = CalendarService.prev_working_day(cur - timedelta(days=1), cal)
                        es_candidate = cur
                    else:
                        es_candidate = base_start

                elif rel_type == "FF":
                    # Predecessor finishes, then successor finishes
                    base_finish = pred.early_finish
                    if lag > 0:
                        cur = base_finish
                        for _ in range(int(round(lag))):
                            cur = CalendarService.next_working_day(cur + timedelta(days=1), cal)
                        req_ef = cur
                    elif lag < 0:
                        cur = base_finish
                        for _ in range(int(round(abs(lag)))):
                            cur = CalendarService.prev_working_day(cur - timedelta(days=1), cal)
                        req_ef = cur
                    else:
                        req_ef = base_finish
                    es_candidate = CalendarService.subtract_working_days(req_ef, duration, cal)

                elif rel_type == "SF":
                    # Predecessor starts, then successor finishes
                    base_start = pred.early_start
                    if lag > 0:
                        cur = base_start
                        for _ in range(int(round(lag))):
                            cur = CalendarService.next_working_day(cur + timedelta(days=1), cal)
                        req_ef = cur
                    elif lag < 0:
                        cur = base_start
                        for _ in range(int(round(abs(lag)))):
                            cur = CalendarService.prev_working_day(cur - timedelta(days=1), cal)
                        req_ef = cur
                    else:
                        req_ef = base_start
                    es_candidate = CalendarService.subtract_working_days(req_ef, duration, cal)

                else:
                    # Default FS
                    next_day = pred.early_finish + timedelta(days=1)
                    es_candidate = CalendarService.next_working_day(next_day, cal)

                candidate_early_starts.append((es_candidate, edge))

            # Base ES is project_start_date (or planned_start if no predecessors)
            if not candidate_early_starts:
                default_start = act.planned_start or project_start_date
                act.early_start = CalendarService.next_working_day(default_start, cal)
                act.driving_predecessor_id = None
                act.driving_predecessor_code = None
            else:
                # Latest required start controls
                max_es, driving_edge = max(candidate_early_starts, key=lambda item: item[0])
                act.early_start = max_es
                act.driving_predecessor_id = driving_edge.predecessor_id
                act.driving_predecessor_code = driving_edge.predecessor_code
                driving_edge.is_driving = True

            # Progress & Data Date aware handling:
            # 1. COMPLETED activities use actual dates if recorded
            if act.status == "COMPLETED":
                if act.actual_start:
                    act.early_start = act.actual_start
                if act.actual_finish:
                    act.early_finish = act.actual_finish
                elif duration <= 0:
                    act.early_finish = act.early_start
                else:
                    act.early_finish = CalendarService.add_working_days(act.early_start, duration, cal)
                act.forecast_start = act.early_start
                act.forecast_finish = act.early_finish
                continue

            # 2. IN_PROGRESS activities: remaining work executed from max(early_start, data_date)
            if act.status == "IN_PROGRESS":
                if act.actual_start:
                    act.forecast_start = act.actual_start
                else:
                    act.forecast_start = act.early_start

                rem_dur = act.remaining_duration
                if rem_dur is None:
                    pct = act.percent_complete or 0.0
                    rem_dur = max(0.0, round(duration * (1.0 - pct / 100.0), 1))

                # Remaining work cannot start prior to data date
                if data_date:
                    dd_working = CalendarService.next_working_day(data_date, cal)
                    rem_start = max(act.early_start, dd_working)
                else:
                    rem_start = act.early_start

                if rem_dur <= 0:
                    act.early_finish = rem_start
                else:
                    act.early_finish = CalendarService.add_working_days(rem_start, rem_dur, cal)
                act.forecast_finish = act.early_finish
                continue

            # 3. NOT_STARTED activities: work cannot start prior to data date
            if data_date:
                dd_working = CalendarService.next_working_day(data_date, cal)
                if act.early_start < dd_working:
                    act.early_start = dd_working

            # Handle Constraints on Start
            if act.constraint_type in ("MANDATORY_START", "START_NO_EARLIER") and act.constraint_date:
                c_date = CalendarService.next_working_day(act.constraint_date, cal)
                if act.constraint_type == "MANDATORY_START":
                    act.early_start = c_date
                elif act.early_start < c_date:
                    act.early_start = c_date

            # Compute Early Finish from Early Start
            if duration <= 0:
                act.early_finish = act.early_start
            else:
                act.early_finish = CalendarService.add_working_days(act.early_start, duration, cal)

            # Handle Constraints on Finish
            if act.constraint_type in ("MANDATORY_FINISH", "FINISH_NO_LATER") and act.constraint_date:
                c_date = CalendarService.prev_working_day(act.constraint_date, cal)
                if act.constraint_type == "MANDATORY_FINISH":
                    act.early_finish = c_date
                    act.early_start = CalendarService.subtract_working_days(act.early_finish, duration, cal)

            act.forecast_start = act.early_start
            act.forecast_finish = act.early_finish

    # -------------------------------------------------------------------------
    # 3. DETERMINISTIC BACKWARD PASS
    # -------------------------------------------------------------------------

    def _backward_pass(
        self,
        nodes: Dict[str, CPMActivityNode],
        edges: List[CPMRelationshipEdge],
        ordered_ids: List[str],
        project_finish_date: date,
        target_finish_date: Optional[date] = None,
    ):
        """
        Calculates Late Finish (LF) and Late Start (LS) for each activity.
        Anchors backward pass to target_finish_date or calculated project_finish_date.
        """
        # Map outgoing edges by predecessor_id
        outgoing_edges: Dict[str, List[CPMRelationshipEdge]] = defaultdict(list)
        for e in edges:
            outgoing_edges[e.predecessor_id].append(e)

        # Reverse topological order
        for act_id in reversed(ordered_ids):
            act = nodes[act_id]
            cal = self.get_calendar(act.calendar_id)
            duration = max(0.0, act.duration)

            candidate_late_finishes: List[date] = []

            for edge in outgoing_edges.get(act_id, []):
                succ = nodes.get(edge.successor_id)
                if not succ or succ.late_start is None or succ.late_finish is None:
                    continue

                rel_type = (edge.relationship_type or "FS").upper()
                lag = edge.lag or 0.0

                if rel_type == "FS":
                    # pred.LF must allow succ.LS after lag
                    standard_prev_finish = CalendarService.prev_working_day(succ.late_start - timedelta(days=1), cal)
                    if lag > 0:
                        cur = standard_prev_finish
                        for _ in range(int(round(lag))):
                            cur = CalendarService.prev_working_day(cur - timedelta(days=1), cal)
                        lf_candidate = cur
                    elif lag < 0:
                        cur = standard_prev_finish
                        for _ in range(int(round(abs(lag)))):
                            cur = CalendarService.next_working_day(cur + timedelta(days=1), cal)
                        lf_candidate = cur
                    else:
                        lf_candidate = standard_prev_finish

                elif rel_type == "SS":
                    # succ.LS must be >= pred.LS + lag
                    base_start = succ.late_start
                    if lag > 0:
                        cur = base_start
                        for _ in range(int(round(lag))):
                            cur = CalendarService.prev_working_day(cur - timedelta(days=1), cal)
                        req_ls = cur
                    elif lag < 0:
                        cur = base_start
                        for _ in range(int(round(abs(lag)))):
                            cur = CalendarService.next_working_day(cur + timedelta(days=1), cal)
                        req_ls = cur
                    else:
                        req_ls = base_start
                    lf_candidate = CalendarService.add_working_days(req_ls, duration, cal)

                elif rel_type == "FF":
                    # succ.LF must be >= pred.LF + lag
                    base_finish = succ.late_finish
                    if lag > 0:
                        cur = base_finish
                        for _ in range(int(round(lag))):
                            cur = CalendarService.prev_working_day(cur - timedelta(days=1), cal)
                        lf_candidate = cur
                    elif lag < 0:
                        cur = base_finish
                        for _ in range(int(round(abs(lag)))):
                            cur = CalendarService.next_working_day(cur + timedelta(days=1), cal)
                        lf_candidate = cur
                    else:
                        lf_candidate = base_finish

                elif rel_type == "SF":
                    # succ.LF must be >= pred.LS + lag
                    base_finish = succ.late_finish
                    if lag > 0:
                        cur = base_finish
                        for _ in range(int(round(lag))):
                            cur = CalendarService.prev_working_day(cur - timedelta(days=1), cal)
                        req_ls = cur
                    elif lag < 0:
                        cur = base_finish
                        for _ in range(int(round(abs(lag)))):
                            cur = CalendarService.next_working_day(cur + timedelta(days=1), cal)
                        req_ls = cur
                    else:
                        req_ls = base_finish
                    lf_candidate = CalendarService.add_working_days(req_ls, duration, cal)

                else:
                    prev_day = succ.late_start - timedelta(days=1)
                    lf_candidate = CalendarService.prev_working_day(prev_day, cal)

                candidate_late_finishes.append(lf_candidate)

            if not candidate_late_finishes:
                # Terminal activity: late finish defaults to project finish (or target finish)
                act.late_finish = target_finish_date or project_finish_date
            else:
                act.late_finish = min(candidate_late_finishes)

            # Apply finish constraint on late finish
            if act.constraint_type in ("MANDATORY_FINISH", "FINISH_NO_LATER") and act.constraint_date:
                c_date = CalendarService.prev_working_day(act.constraint_date, cal)
                if act.constraint_type == "MANDATORY_FINISH":
                    act.late_finish = c_date
                elif act.late_finish > c_date:
                    act.late_finish = c_date

            # Late Start from Late Finish
            if duration <= 0:
                act.late_start = act.late_finish
            else:
                act.late_start = CalendarService.subtract_working_days(act.late_finish, duration, cal)

            # Apply start constraint on late start
            if act.constraint_type in ("MANDATORY_START", "START_NO_EARLIER") and act.constraint_date:
                c_date = CalendarService.next_working_day(act.constraint_date, cal)
                if act.constraint_type == "MANDATORY_START":
                    act.late_start = c_date
                elif act.late_start < c_date:
                    act.late_start = c_date

    # -------------------------------------------------------------------------
    # 4. FLOAT, CRITICALITY, & PATH ANALYSIS
    # -------------------------------------------------------------------------

    def _calculate_floats_and_paths(
        self,
        nodes: Dict[str, CPMActivityNode],
        edges: List[CPMRelationshipEdge],
        ordered_ids: List[str],
    ) -> Tuple[List[str], List[str], List[str], List[str], List[List[str]], List[str]]:
        """
        Calculates Total Float (TF), Free Float (FF), Critical Path, Float Paths,
        and high float / open-end warnings.
        """
        outgoing_edges: Dict[str, List[CPMRelationshipEdge]] = defaultdict(list)
        incoming_edges: Dict[str, List[CPMRelationshipEdge]] = defaultdict(list)
        for e in edges:
            outgoing_edges[e.predecessor_id].append(e)
            incoming_edges[e.successor_id].append(e)

        critical_activities: List[str] = []
        near_critical_activities: List[str] = []
        negative_float_activities: List[str] = []
        high_float_activities: List[str] = []

        for act_id, act in nodes.items():
            cal = self.get_calendar(act.calendar_id)
            preds = incoming_edges.get(act_id, [])
            succs = outgoing_edges.get(act_id, [])
            act.is_open_start = len(preds) == 0
            act.is_open_finish = len(succs) == 0

            # Compute finish variance: Forecast/Actual Finish - Planned Finish
            # Never use Today's date!
            if act.planned_finish:
                ref_finish = act.forecast_finish or act.actual_finish or act.early_finish
                if ref_finish:
                    act.finish_variance = float((ref_finish - act.planned_finish).days)

            # Total Float = LS - ES (or LF - EF) in working days
            if act.late_start and act.early_start:
                if act.late_start >= act.early_start:
                    tf = CalendarService.working_days_between(act.early_start, act.late_start, cal) - 1.0
                    act.total_float = max(0.0, round(tf, 2))
                else:
                    tf = -CalendarService.working_days_between(act.late_start, act.early_start, cal) + 1.0
                    act.total_float = min(0.0, round(tf, 2))
            else:
                act.total_float = 0.0

            # Free Float = min over successors of slack before successor's Early Start
            succ_edges = outgoing_edges.get(act_id, [])
            if not succ_edges:
                act.free_float = act.total_float
            else:
                free_floats: List[float] = []
                for edge in succ_edges:
                    succ = nodes.get(edge.successor_id)
                    if not succ or not succ.early_start or not act.early_finish:
                        continue
                    rel_type = (edge.relationship_type or "FS").upper()
                    lag = edge.lag or 0.0

                    if rel_type == "FS":
                        latest_pred_finish = CalendarService.prev_working_day(succ.early_start - timedelta(days=1), cal)
                        if lag > 0:
                            for _ in range(int(round(lag))):
                                latest_pred_finish = CalendarService.prev_working_day(latest_pred_finish - timedelta(days=1), cal)
                        elif lag < 0:
                            for _ in range(int(round(abs(lag)))):
                                latest_pred_finish = CalendarService.next_working_day(latest_pred_finish + timedelta(days=1), cal)

                        if act.early_finish >= latest_pred_finish:
                            slack = 0.0
                        else:
                            slack = CalendarService.working_days_between(act.early_finish, latest_pred_finish, cal) - 1.0

                    elif rel_type == "SS":
                        if lag > 0:
                            allowed_start = act.early_start
                            for _ in range(int(round(lag))):
                                allowed_start = CalendarService.next_working_day(allowed_start + timedelta(days=1), cal)
                        else:
                            allowed_start = act.early_start
                        if succ.early_start <= allowed_start:
                            slack = 0.0
                        else:
                            slack = CalendarService.working_days_between(allowed_start, succ.early_start, cal) - 1.0

                    elif rel_type == "FF":
                        if lag > 0:
                            allowed_finish = act.early_finish
                            for _ in range(int(round(lag))):
                                allowed_finish = CalendarService.next_working_day(allowed_finish + timedelta(days=1), cal)
                        else:
                            allowed_finish = act.early_finish
                        if succ.early_finish <= allowed_finish:
                            slack = 0.0
                        else:
                            slack = CalendarService.working_days_between(allowed_finish, succ.early_finish, cal) - 1.0

                    else:
                        slack = max(0.0, act.total_float or 0.0)

                    free_floats.append(round(slack, 2))
                    edge.relationship_float = round(slack, 2)

                act.free_float = round(min(free_floats), 2) if free_floats else act.total_float

            # Classify Criticality and Float Warnings
            tf_val = act.total_float if act.total_float is not None else 0.0
            if tf_val < 0.0:
                act.has_negative_float = True
                act.is_critical = True
                negative_float_activities.append(act.activity_code)
                critical_activities.append(act.activity_code)
                act.float_warning = "NEGATIVE_FLOAT"
                act.float_explanation = "Negative float: activity completion exceeds required finish or constraint."
            elif tf_val == 0.0:
                act.is_critical = True
                critical_activities.append(act.activity_code)
            elif tf_val <= self.near_critical_threshold:
                act.is_near_critical = True
                near_critical_activities.append(act.activity_code)

            if tf_val > self.high_float_threshold:
                high_float_activities.append(act.activity_code)
                if act.is_open_start and act.is_open_finish:
                    act.float_warning = "DISCONNECTED"
                    act.float_explanation = "Activity is completely disconnected (no predecessor or successor links)."
                elif act.is_open_finish:
                    act.float_warning = "OPEN_FINISH"
                    act.float_explanation = "Terminal activity with no successor logic; late date anchored to project finish."
                elif act.is_open_start:
                    act.float_warning = "OPEN_START"
                    act.float_explanation = "Activity has no predecessor logic."
                else:
                    act.float_warning = "HIGH_FLOAT"
                    act.float_explanation = "Activity has large schedule slack on a non-controlling path."

        # ---------------------------------------------------------------------
        # 5. LONGEST PATH / CRITICAL PATH CONTINUOUS CHAIN
        # ---------------------------------------------------------------------
        critical_path = self._calculate_longest_path(nodes, edges)

        # ---------------------------------------------------------------------
        # 6. MULTIPLE FLOAT PATHS
        # ---------------------------------------------------------------------
        float_paths = self._calculate_float_paths(nodes, edges, critical_path)

        return (
            critical_path,
            critical_activities,
            near_critical_activities,
            negative_float_activities,
            float_paths,
            high_float_activities,
        )

    def _calculate_longest_path(
        self,
        nodes: Dict[str, CPMActivityNode],
        edges: List[CPMRelationshipEdge],
    ) -> List[str]:
        """
        Determines the continuous longest path from project start to finish using driving relationships.
        """
        if not nodes:
            return []

        # Find terminal activities with max early finish
        max_finish_node = max(nodes.values(), key=lambda n: n.early_finish or date.min)
        terminal_nodes = [
            n for n in nodes.values()
            if (n.early_finish == max_finish_node.early_finish and (n.total_float or 0.0) <= 0.0)
        ]
        if not terminal_nodes:
            terminal_nodes = [max_finish_node]

        curr = terminal_nodes[0]
        path: List[str] = [curr.activity_code]

        # Trace backward via driving predecessor
        visited: Set[str] = {curr.id}
        while curr.driving_predecessor_id:
            pred_id = curr.driving_predecessor_id
            if pred_id in visited or pred_id not in nodes:
                break
            visited.add(pred_id)
            curr = nodes[pred_id]
            path.append(curr.activity_code)

        return list(reversed(path))

    def _calculate_float_paths(
        self,
        nodes: Dict[str, CPMActivityNode],
        edges: List[CPMRelationshipEdge],
        longest_path: List[str],
    ) -> List[List[str]]:
        """
        Groups schedule activities into ordered float paths (Path 1 = Critical, Path 2 = Near-Critical, etc.).
        """
        paths: List[List[str]] = []
        if longest_path:
            paths.append(longest_path)

        # Group remaining activities by total float ranges
        sorted_acts = sorted(
            [n for n in nodes.values() if n.activity_code not in set(longest_path)],
            key=lambda x: (x.total_float or 0.0, x.early_start or date.min),
        )

        current_path: List[str] = []
        current_float: Optional[float] = None

        for a in sorted_acts:
            tf = a.total_float or 0.0
            if current_float is None or abs(tf - current_float) <= 1.0:
                current_path.append(a.activity_code)
                current_float = tf
            else:
                if current_path:
                    paths.append(current_path)
                current_path = [a.activity_code]
                current_float = tf

        if current_path:
            paths.append(current_path)

        return paths

    # -------------------------------------------------------------------------
    # 7. MAIN ENGINE ENTRYPOINT
    # -------------------------------------------------------------------------

    def calculate(
        self,
        activities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        project_start_date: Optional[Union[date, datetime, str]] = None,
        target_finish_date: Optional[Union[date, datetime, str]] = None,
        data_date: Optional[Union[date, datetime, str]] = None,
        must_finish_by_date: Optional[Union[date, datetime, str]] = None,
    ) -> CPMResult:
        """
        Runs the full deterministic CPM pass.
        Returns CPMResult containing dates, floats, paths, and graph statistics.
        """
        # Parse inputs into domain nodes & edges
        nodes: Dict[str, CPMActivityNode] = {}
        for a in activities:
            act_id = str(a.get("id") or a.get("activity_code"))
            code = str(a.get("activity_code") or act_id)
            dur = float(a.get("original_duration") or a.get("duration") or 0.0)

            def to_d(v: Optional[Union[date, datetime, str]]) -> Optional[date]:
                if not v:
                    return None
                try:
                    return CalendarService._to_date(v)
                except Exception:
                    return None

            nodes[act_id] = CPMActivityNode(
                id=act_id,
                activity_code=code,
                name=str(a.get("name") or code),
                duration=dur,
                planned_start=to_d(a.get("planned_start")),
                planned_finish=to_d(a.get("planned_finish")),
                actual_start=to_d(a.get("actual_start")),
                actual_finish=to_d(a.get("actual_finish")),
                calendar_id=a.get("calendar"),
                constraint_type=a.get("constraint_type"),
                constraint_date=to_d(a.get("constraint_date")),
                status=str(a.get("status") or "NOT_STARTED").upper(),
                percent_complete=float(a.get("percent_complete") or 0.0),
                remaining_duration=float(a.get("remaining_duration")) if a.get("remaining_duration") is not None else None,
            )

        edges: List[CPMRelationshipEdge] = []
        for idx, r in enumerate(relationships):
            pid = str(r.get("predecessor_id") or r.get("predecessor_code"))
            sid = str(r.get("successor_id") or r.get("successor_code"))
            pcode = str(r.get("predecessor_code") or (nodes[pid].activity_code if pid in nodes else pid))
            scode = str(r.get("successor_code") or (nodes[sid].activity_code if sid in nodes else sid))
            rel_type = (r.get("relationship_type") or "FS").upper()
            lag = float(r.get("lag") or 0.0)

            edges.append(
                CPMRelationshipEdge(
                    id=str(r.get("id") or f"rel-{idx}"),
                    predecessor_id=pid,
                    successor_id=sid,
                    predecessor_code=pcode,
                    successor_code=scode,
                    relationship_type=rel_type,
                    lag=lag,
                )
            )

        # Basic graph connectivity diagnostics
        preds_set = {e.predecessor_id for e in edges}
        succs_set = {e.successor_id for e in edges}
        isolated = [n.activity_code for nid, n in nodes.items() if nid not in preds_set and nid not in succs_set]
        open_starts = [n.activity_code for nid, n in nodes.items() if nid not in succs_set]
        open_finishes = [n.activity_code for nid, n in nodes.items() if nid not in preds_set]

        if not nodes:
            return CPMResult(
                project_start=None,
                project_finish=None,
                project_duration_days=0.0,
                activities={},
                relationships=[],
                critical_path=[],
                critical_activities=[],
                near_critical_activities=[],
                negative_float_activities=[],
                float_paths=[],
                isolated_activities=[],
                open_start_activities=[],
                open_finish_activities=[],
            )

        # 1. Topological ordering & cycle check
        try:
            ordered_ids = self.topological_sort(nodes, edges)
        except CycleDetectedException as e:
            logger.error(f"Cycle detected in schedule network: {e.cycle_nodes}")
            return CPMResult(
                project_start=None,
                project_finish=None,
                project_duration_days=0.0,
                activities=nodes,
                relationships=edges,
                critical_path=[],
                critical_activities=[],
                near_critical_activities=[],
                negative_float_activities=[],
                float_paths=[],
                isolated_activities=isolated,
                open_start_activities=open_starts,
                open_finish_activities=open_finishes,
                cycles_detected=True,
                error=str(e),
            )

        # Resolve Project Start Date
        start_d: date
        if project_start_date:
            start_d = CalendarService._to_date(project_start_date)
        else:
            # Earliest planned start among activities, or today
            planned_starts = [n.planned_start for n in nodes.values() if n.planned_start]
            start_d = min(planned_starts) if planned_starts else date.today()

        # Resolve Data Date
        d_date = CalendarService._to_date(data_date) if data_date else None

        # 2. Forward pass
        self._forward_pass(nodes, edges, ordered_ids, start_d, data_date=d_date)

        # Project Finish Date from max early finish
        calculated_finish = max((n.early_finish for n in nodes.values() if n.early_finish), default=start_d)
        
        # Target finish / must finish by date
        target_f = CalendarService._to_date(must_finish_by_date or target_finish_date) if (must_finish_by_date or target_finish_date) else None

        # 3. Backward pass
        self._backward_pass(nodes, edges, ordered_ids, calculated_finish, target_f)

        # 4. Total float, free float, paths, and warnings
        (
            critical_path,
            critical_acts,
            near_crit_acts,
            neg_float_acts,
            float_paths,
            high_float_acts,
        ) = self._calculate_floats_and_paths(nodes, edges, ordered_ids)

        cal_default = self.default_calendar
        proj_dur = CalendarService.working_days_between(start_d, calculated_finish, cal_default)

        return CPMResult(
            project_start=start_d,
            project_finish=calculated_finish,
            project_duration_days=proj_dur,
            activities=nodes,
            relationships=edges,
            critical_path=critical_path,
            critical_activities=critical_acts,
            near_critical_activities=near_crit_acts,
            negative_float_activities=neg_float_acts,
            float_paths=float_paths,
            isolated_activities=isolated,
            open_start_activities=open_starts,
            open_finish_activities=open_finishes,
            high_float_activities=high_float_acts,
            data_date=d_date,
            must_finish_by_date=target_f,
            near_critical_threshold=self.near_critical_threshold,
            cycles_detected=False,
        )
