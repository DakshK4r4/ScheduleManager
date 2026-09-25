from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.domain.database import get_db
from app.domain.models import (
    Activity,
    ActivityRelationship,
    Artifact,
    ExecutionEvent,
    Project,
    ScheduleAuditLog,
    WBSNode,
)

router = APIRouter(tags=["Export & Audit"])


@router.get("/api/v1/projects/{project_id}/audit-trail")
def get_project_audit_trail(project_id: str, db: Session = Depends(get_db)):
    """
    Retrieve immutable audit log records for a project, showing full end-to-end
    provenance from schedule update -> ExecutionEvent -> Artifact -> MinIO storage key.
    """
    logs = (
        db.query(ScheduleAuditLog)
        .filter(ScheduleAuditLog.project_id == project_id)
        .order_by(ScheduleAuditLog.timestamp.desc())
        .all()
    )

    results = []
    for log in logs:
        artifact = db.query(Artifact).filter(Artifact.id == log.artifact_id).first() if log.artifact_id else None
        event = db.query(ExecutionEvent).filter(ExecutionEvent.id == log.execution_event_id).first() if log.execution_event_id else None
        activity = db.query(Activity).filter(Activity.id == log.activity_id).first() if log.activity_id else None

        results.append({
            "id": log.id,
            "project_id": log.project_id,
            "activity_id": log.activity_id,
            "activity_code": activity.activity_code if activity else None,
            "activity_name": activity.name if activity else None,
            "action": log.action,
            "user_id": log.user_id,
            "timestamp": log.timestamp.isoformat(),
            "previous_state": log.previous_state,
            "new_state": log.new_state,
            "execution_event": {
                "id": event.id if event else None,
                "verbatim_excerpt": event.verbatim_excerpt if event else None,
                "execution_date": event.execution_date.strftime("%Y-%m-%d") if event else None,
            } if event else None,
            "artifact": {
                "id": artifact.id if artifact else None,
                "original_filename": artifact.original_filename if artifact else None,
                "sha256": artifact.sha256 if artifact else None,
                "storage_key": artifact.storage_key if artifact else None,
                "storage_bucket": artifact.storage_bucket if artifact else None,
            } if artifact else None,
        })

    return {"project_id": project_id, "total_records": len(results), "audit_trail": results}


@router.get("/api/v1/projects/{project_id}/export/xer")
def export_p6_xer(project_id: str, db: Session = Depends(get_db)):
    """
    Export current authoritative project schedule into Primavera P6 .xer format,
    preserving PROJECT, PROJWBS, TASK, and TASKPRED tables with full logic and hierarchy.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found.",
        )

    wbs_nodes = db.query(WBSNode).filter(WBSNode.project_id == project_id).order_by(WBSNode.code).all()
    activities = db.query(Activity).filter(Activity.project_id == project_id).order_by(Activity.activity_code).all()
    relationships = (
        db.query(ActivityRelationship)
        .filter(ActivityRelationship.project_id == project_id)
        .all()
    )

    def fmt_dt(dt: Any) -> str:
        if not dt:
            return ""
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except Exception:
                return dt
        return dt.strftime("%Y-%m-%d %H:%M")

    # 1. Map WBS UUIDs to integer wbs_id
    wbs_uuid_to_int: Dict[str, int] = {}
    if not wbs_nodes:
        # Create a synthetic default root WBS node if none exist
        wbs_uuid_to_int["default"] = 1
    else:
        for idx, w in enumerate(wbs_nodes, start=10):
            wbs_uuid_to_int[w.id] = idx

    # 2. Map Activity UUIDs to integer task_id
    act_uuid_to_int: Dict[str, int] = {}
    for idx, act in enumerate(activities, start=1001):
        act_uuid_to_int[act.id] = idx

    # Build P6 XER lines
    lines: List[str] = [
        "ERMHDR\t19.12\t1984-01-01\tXER\tP6 Professional\r\n",
        "%T\tPROJECT\r\n",
        "%F\tproj_id\tproj_short_name\tplan_start_date\tplan_end_date\tlast_recalc_date\ttarget_start_date\ttarget_end_date\r\n",
        f"%R\t1\t{project.project_code}\t{fmt_dt(project.planned_start)}\t{fmt_dt(project.planned_finish)}\t{fmt_dt(project.data_date)}\t{fmt_dt(project.planned_start)}\t{fmt_dt(project.planned_finish)}\r\n",
    ]

    # PROJWBS table
    lines.append("%T\tPROJWBS\r\n")
    lines.append("%F\twbs_id\tproj_id\twbs_short_name\twbs_name\tparent_wbs_id\r\n")
    if not wbs_nodes:
        lines.append(f"%R\t1\t1\t{project.project_code}\t{project.name}\t\r\n")
    else:
        for w in wbs_nodes:
            w_int = wbs_uuid_to_int[w.id]
            parent_int = str(wbs_uuid_to_int[w.parent_id]) if w.parent_id and w.parent_id in wbs_uuid_to_int else ""
            lines.append(f"%R\t{w_int}\t1\t{w.code}\t{w.name}\t{parent_int}\r\n")

    # TASK table
    lines.append("%T\tTASK\r\n")
    lines.append(
        "%F\ttask_id\tproj_id\twbs_id\ttask_code\ttask_name\ttask_type\tstatus_code\t"
        "target_drtn_hr_cnt\tremain_drtn_hr_cnt\ttarget_start_date\ttarget_end_date\t"
        "act_start_date\tact_end_date\tearly_start_date\tearly_end_date\t"
        "phys_percent_comp\tclndr_id\tcstr_type\tcstr_date\r\n"
    )

    default_wbs_int = list(wbs_uuid_to_int.values())[0]
    for act in activities:
        task_int = act_uuid_to_int[act.id]
        wbs_int = wbs_uuid_to_int.get(act.wbs_id, default_wbs_int) if act.wbs_id else default_wbs_int
        status_code = (
            "TK_Complete"
            if act.status == "COMPLETED"
            else ("TK_Active" if act.status == "IN_PROGRESS" else "TK_NotStart")
        )
        target_dur_hr = str(int(round(act.original_duration * 8.0))) if act.original_duration is not None else ""
        remain_dur_hr = str(int(round(act.remaining_duration * 8.0))) if act.remaining_duration is not None else target_dur_hr
        pct = act.percent_complete or 0.0

        cstr_t = act.constraint_type or ""
        cstr_d = fmt_dt(act.constraint_date)

        lines.append(
            f"%R\t{task_int}\t1\t{wbs_int}\t{act.activity_code}\t{act.name}\t{act.activity_type or 'TT_Task'}\t"
            f"{status_code}\t{target_dur_hr}\t{remain_dur_hr}\t{fmt_dt(act.planned_start)}\t{fmt_dt(act.planned_finish)}\t"
            f"{fmt_dt(act.actual_start)}\t{fmt_dt(act.actual_finish)}\t{fmt_dt(act.early_start)}\t{fmt_dt(act.early_finish)}\t"
            f"{pct:.2f}\t{act.calendar or ''}\t{cstr_t}\t{cstr_d}\r\n"
        )

    # TASKPRED table
    lines.append("%T\tTASKPRED\r\n")
    lines.append("%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt\r\n")

    rel_type_map = {
        "FS": "PR_FS",
        "SS": "PR_SS",
        "FF": "PR_FF",
        "SF": "PR_SF",
    }

    pred_idx = 5001
    for rel in relationships:
        succ_int = act_uuid_to_int.get(rel.successor_id)
        pred_int = act_uuid_to_int.get(rel.predecessor_id)
        if succ_int and pred_int:
            pred_type = rel_type_map.get((rel.relationship_type or "FS").upper(), "PR_FS")
            lag_hr = int(round((rel.lag or 0.0) * 8.0))
            lines.append(f"%R\t{pred_idx}\t{succ_int}\t{pred_int}\t{pred_type}\t{lag_hr}\r\n")
            pred_idx += 1

    # MEMOTYPE and TASKMEMO
    memos_to_export = []
    memo_id = 9001
    for act in activities:
        if act.notes and act.notes.strip():
            task_int = act_uuid_to_int.get(act.id)
            if task_int:
                cleaned_note = act.notes.strip().replace("\t", " ").replace("\r", " ").replace("\n", " ")
                memos_to_export.append((memo_id, task_int, cleaned_note))
                memo_id += 1

    if memos_to_export:
        lines.append("%T\tMEMOTYPE\r\n")
        lines.append("%F\tmemo_type_id\tmemo_type_name\r\n")
        lines.append("%R\t1\tField Execution Notes\r\n")
        lines.append("%T\tTASKMEMO\r\n")
        lines.append("%F\tmemo_id\ttask_id\tmemo_type_id\ttask_memo\r\n")
        for m_id, t_id, note_text in memos_to_export:
            lines.append(f"%R\t{m_id}\t{t_id}\t1\t{note_text}\r\n")

    lines.append("%E\r\n")
    xer_content = "".join(lines)

    return Response(
        content=xer_content.encode("utf-8"),
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{project.project_code}_updated.xer"'
        },
    )
