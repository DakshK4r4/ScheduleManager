from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.domain.models import (
    Activity,
    ActualProgressLedger,
    Artifact,
    DomainOutbox,
    ExecutionEvent,
    ReviewDecision,
    ScheduleAuditLog,
)
from app.services.validation_service import ValidationException, ValidationService

logger = logging.getLogger("schedule_update_service")


class ScheduleUpdateService:
    @classmethod
    def apply_event_progress(
        cls,
        db: Session,
        event_id: str,
        activity_id: Optional[str] = None,
        user_id: str = "system-auto",
        override_percent: Optional[float] = None,
        action_name: str = "AUTO_LINK_PROGRESS",
        quantity_semantics: str = "INCREMENTAL",
        commit: bool = True,
    ) -> Activity:
        """
        Safely update an activity's progress from an approved/auto-linked ExecutionEvent:
        1. Derive physical progress and incremental values (supporting incremental & cumulative semantics)
        2. Insert into append-only actual_progress_ledger
        3. Validate and mutate Activity (enforcing CPM firewall on baseline dates)
        4. Record ScheduleAuditLog with full provenance to artifact_id and MinIO
        5. Insert task into domain_outbox for external writeback
        6. Commit transaction if commit=True, or flush if caller owns transaction boundary
        """
        event = db.query(ExecutionEvent).filter(ExecutionEvent.id == event_id).first()
        if not event:
            raise ValueError(f"ExecutionEvent {event_id} not found.")

        target_act_id = activity_id or event.matched_activity_id
        activity = db.query(Activity).filter(Activity.id == target_act_id).first()
        if not activity:
            raise ValueError(f"Activity {target_act_id} not found.")

        if activity.project_id != event.project_id:
            raise ValueError(
                f"Security violation: Target activity {target_act_id} (project {activity.project_id}) "
                f"does not belong to event {event_id} (project {event.project_id})."
            )

        # Check idempotency: Has this event already been applied to this activity?
        existing_ledger = (
            db.query(ActualProgressLedger)
            .filter(
                ActualProgressLedger.activity_id == target_act_id,
                ActualProgressLedger.execution_event_id == event_id,
            )
            .first()
        )
        if existing_ledger:
            logger.info(f"Event {event_id} already applied to activity {target_act_id}. Returning current activity state.")
            return activity

        # Current state before update
        prev_pct = activity.percent_complete or 0.0
        prev_status = activity.status
        prev_state_json = json.dumps({
            "percent_complete": prev_pct,
            "status": prev_status,
            "actual_start": activity.actual_start.isoformat() if activity.actual_start else None,
            "actual_finish": activity.actual_finish.isoformat() if activity.actual_finish else None,
        })

        # Calculate new cumulative percent complete and installed incremental quantity
        installed_incremental_qty = event.quantity
        if override_percent is not None:
            new_pct = max(0.0, min(100.0, float(override_percent)))
        elif event.status_reported == "COMPLETED":
            new_pct = 100.0
        elif event.quantity is not None and activity.planned_quantity and activity.planned_quantity > 0:
            if quantity_semantics == "CUMULATIVE":
                q_planned = float(activity.planned_quantity)
                q_prev = round((prev_pct / 100.0) * q_planned, 4)
                q_cum = float(event.quantity)

                if q_cum < q_prev:
                    raise ValidationException(
                        f"Reported cumulative quantity ({q_cum} {event.unit or ''}) is less than previously recorded installed quantity ({q_prev} {event.unit or ''}). Execution reporting cannot decrease progress."
                    )
                elif q_cum == q_prev:
                    installed_incremental_qty = 0.0
                    new_pct = prev_pct
                elif q_cum > q_planned:
                    installed_incremental_qty = round(q_cum - q_prev, 4)
                    new_pct = 100.0
                else:
                    installed_incremental_qty = round(q_cum - q_prev, 4)
                    new_pct = min(100.0, (q_cum / q_planned) * 100.0)
            else:
                qty_ratio = (event.quantity / activity.planned_quantity) * 100.0
                new_pct = min(100.0, prev_pct + qty_ratio)
        else:
            # Shift progress increment (bounded)
            new_pct = min(100.0, prev_pct + 25.0 if prev_pct < 75.0 else 100.0)

        incremental_pct = round(new_pct - prev_pct, 2)
        new_pct = round(new_pct, 2)

        # Derive activity status
        if new_pct >= 100.0:
            new_status = "COMPLETED"
        elif new_pct > 0.0:
            new_status = "IN_PROGRESS"
        else:
            new_status = prev_status

        # 1. Insert into append-only ActualProgressLedger
        ledger_entry = ActualProgressLedger(
            project_id=activity.project_id,
            activity_id=activity.id,
            execution_event_id=event.id,
            reporting_date=event.execution_date,
            installed_quantity=installed_incremental_qty,
            unit_of_measure=event.unit,
            incremental_percent=incremental_pct,
            cumulative_percent=new_pct,
        )
        db.add(ledger_entry)

        # 2. Prepare Activity update (strictly preserving planned_start and planned_finish)
        updates: Dict[str, Any] = {
            "percent_complete": new_pct,
            "status": new_status,
        }
        if not activity.actual_start:
            activity.actual_start = event.execution_date
        if new_pct >= 100.0 and not activity.actual_finish:
            activity.actual_finish = event.execution_date

        # Validate with existing ScheduleManager validation service
        ValidationService.validate_activity_update(
            updates,
            activity.planned_start,
            activity.planned_finish,
        )

        activity.percent_complete = new_pct
        activity.status = new_status
        activity.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        new_state_json = json.dumps({
            "percent_complete": activity.percent_complete,
            "status": activity.status,
            "actual_start": activity.actual_start.isoformat() if activity.actual_start else None,
            "actual_finish": activity.actual_finish.isoformat() if activity.actual_finish else None,
        })

        # 3. Create ScheduleAuditLog referencing artifact_id & event_id
        audit_entry = ScheduleAuditLog(
            project_id=activity.project_id,
            activity_id=activity.id,
            execution_event_id=event.id,
            artifact_id=event.artifact_id,
            action=action_name,
            previous_state=prev_state_json,
            new_state=new_state_json,
            user_id=user_id,
        )
        db.add(audit_entry)

        # 4. Insert into DomainOutbox for external schedule integration
        outbox_entry = DomainOutbox(
            event_type="SCHEDULE_PROGRESS_UPDATED",
            aggregate_id=activity.id,
            payload=json.dumps({
                "activity_id": activity.id,
                "activity_code": activity.activity_code,
                "project_id": activity.project_id,
                "percent_complete": new_pct,
                "status": new_status,
                "artifact_id": event.artifact_id,
                "execution_event_id": event.id,
                "execution_date": event.execution_date.isoformat(),
            }),
            status="PENDING",
        )
        db.add(outbox_entry)

        # 5. Update ExecutionEvent status to APPLIED
        event.status = "APPLIED"
        event.matched_activity_id = activity.id

        if commit:
            db.commit()
            db.refresh(activity)
        else:
            db.flush()
        return activity
