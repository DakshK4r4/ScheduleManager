from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from datetime import timezone
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session, joinedload

from app.domain.models import Activity, WBSNode
from app.schemas.activity import ActivityResponse


class ActivityRepository:
    @staticmethod
    def get_by_id(db: Session, activity_id: str) -> Optional[Activity]:
        return db.query(Activity).options(joinedload(Activity.wbs_node)).filter(Activity.id == activity_id).first()

    @staticmethod
    def get_by_code(db: Session, project_id: str, activity_code: str) -> Optional[Activity]:
        return (
            db.query(Activity)
            .filter(Activity.project_id == project_id, Activity.activity_code == activity_code)
            .first()
        )

    @staticmethod
    def filter_activities(
        db: Session,
        project_id: str,
        activity_code: Optional[str] = None,
        name: Optional[str] = None,
        search: Optional[str] = None,
        wbs_id: Optional[str] = None,
        status: Optional[str] = None,
        start_date_from: Optional[datetime] = None,
        start_date_to: Optional[datetime] = None,
        finish_date_from: Optional[datetime] = None,
        finish_date_to: Optional[datetime] = None,
        percent_min: Optional[float] = None,
        percent_max: Optional[float] = None,
        sort_by: str = "activity_code",
        sort_dir: str = "asc",
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ActivityResponse], int]:
        query = (
            db.query(Activity)
            .outerjoin(WBSNode, Activity.wbs_id == WBSNode.id)
            .filter(Activity.project_id == project_id)
        )

        if search:
            query = query.filter(
                or_(
                    Activity.activity_code.ilike(f"%{search}%"),
                    Activity.name.ilike(f"%{search}%"),
                )
            )
        elif activity_code and name and activity_code == name:
            query = query.filter(
                or_(
                    Activity.activity_code.ilike(f"%{activity_code}%"),
                    Activity.name.ilike(f"%{name}%"),
                )
            )
        else:
            if activity_code:
                query = query.filter(Activity.activity_code.ilike(f"%{activity_code}%"))
            if name:
                query = query.filter(Activity.name.ilike(f"%{name}%"))
        if wbs_id:
            query = query.filter(Activity.wbs_id == wbs_id)
        if status:
            query = query.filter(Activity.status == status.upper())
        if start_date_from:
            query = query.filter(Activity.planned_start >= start_date_from)
        if start_date_to:
            query = query.filter(Activity.planned_start <= start_date_to)
        if finish_date_from:
            query = query.filter(Activity.planned_finish >= finish_date_from)
        if finish_date_to:
            query = query.filter(Activity.planned_finish <= finish_date_to)
        if percent_min is not None:
            query = query.filter(Activity.percent_complete >= percent_min)
        if percent_max is not None:
            query = query.filter(Activity.percent_complete <= percent_max)

        total = query.count()

        # Sorting
        sort_column_map = {
            "activity_code": Activity.activity_code,
            "name": Activity.name,
            "status": Activity.status,
            "planned_start": Activity.planned_start,
            "planned_finish": Activity.planned_finish,
            "original_duration": Activity.original_duration,
            "percent_complete": Activity.percent_complete,
            "wbs": WBSNode.code,
        }
        sort_col = sort_column_map.get(sort_by, Activity.activity_code)
        order_fn = desc if sort_dir.lower() == "desc" else asc
        query = query.order_by(order_fn(sort_col))

        # Pagination
        offset = max(0, (page - 1) * page_size)
        acts = query.offset(offset).limit(page_size).all()

        results = []
        for a in acts:
            wbs_code = a.wbs_node.code if a.wbs_node else None
            wbs_name = a.wbs_node.name if a.wbs_node else None
            results.append(
                ActivityResponse(
                    id=a.id,
                    project_id=a.project_id,
                    wbs_id=a.wbs_id,
                    wbs_code=wbs_code,
                    wbs_name=wbs_name,
                    activity_code=a.activity_code,
                    name=a.name,
                    activity_type=a.activity_type,
                    status=a.status,
                    planned_start=a.planned_start,
                    planned_finish=a.planned_finish,
                    actual_start=a.actual_start,
                    actual_finish=a.actual_finish,
                    original_duration=a.original_duration,
                    remaining_duration=a.remaining_duration,
                    percent_complete=a.percent_complete,
                    calendar=a.calendar,
                    location_code=a.location_code,
                    discipline=a.discipline,
                    contractor_name=a.contractor_name,
                    planned_quantity=a.planned_quantity,
                    quantity_unit=a.quantity_unit,
                    early_start=a.early_start,
                    early_finish=a.early_finish,
                    late_start=a.late_start,
                    late_finish=a.late_finish,
                    total_float=a.total_float,
                    free_float=a.free_float,
                    is_critical=a.is_critical,
                    driving_predecessor_id=a.driving_predecessor_id,
                    constraint_type=a.constraint_type,
                    constraint_date=a.constraint_date,
                    created_at=a.created_at,
                    updated_at=a.updated_at,
                )
            )

        return results, total

    @staticmethod
    def create(db: Session, activity: Activity) -> Activity:
        db.add(activity)
        db.flush()
        return activity

    @staticmethod
    def update(db: Session, activity: Activity, updates: Dict[str, Any]) -> Activity:
        for field, val in updates.items():
            if hasattr(activity, field) and val is not None:
                setattr(activity, field, val)
        activity.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.flush()
        return activity

    @staticmethod
    def delete(db: Session, activity_id: str) -> bool:
        act = db.query(Activity).filter(Activity.id == activity_id).first()
        if not act:
            return False
        db.delete(act)
        db.commit()
        return True
