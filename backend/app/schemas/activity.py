from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActivityBase(BaseModel):
    activity_code: str = Field(..., description="Unique activity code inside project")
    name: str = Field(..., description="Activity name")
    wbs_id: Optional[str] = None
    activity_type: Optional[str] = "TT_Task"
    status: str = Field("NOT_STARTED", description="NOT_STARTED, IN_PROGRESS, or COMPLETED")
    planned_start: Optional[datetime] = None
    planned_finish: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_finish: Optional[datetime] = None
    original_duration: Optional[float] = None
    remaining_duration: Optional[float] = None
    percent_complete: Optional[float] = Field(0.0, description="0 to 100")
    calendar: Optional[str] = None
    location_code: Optional[str] = None
    discipline: Optional[str] = None
    contractor_name: Optional[str] = None
    planned_quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    early_start: Optional[datetime] = None
    early_finish: Optional[datetime] = None
    late_start: Optional[datetime] = None
    late_finish: Optional[datetime] = None
    total_float: Optional[float] = None
    free_float: Optional[float] = None
    is_critical: Optional[bool] = False
    driving_predecessor_id: Optional[str] = None
    constraint_type: Optional[str] = None
    constraint_date: Optional[datetime] = None

    @field_validator("percent_complete")
    @classmethod
    def validate_percent(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 100.0):
            raise ValueError("Percentage complete must be between 0 and 100.")
        return v


class ActivityCreate(ActivityBase):
    pass


class ActivityUpdate(BaseModel):
    activity_code: Optional[str] = None
    name: Optional[str] = None
    wbs_id: Optional[str] = None
    activity_type: Optional[str] = None
    status: Optional[str] = None
    planned_start: Optional[datetime] = None
    planned_finish: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_finish: Optional[datetime] = None
    original_duration: Optional[float] = None
    remaining_duration: Optional[float] = None
    percent_complete: Optional[float] = None
    calendar: Optional[str] = None
    location_code: Optional[str] = None
    discipline: Optional[str] = None
    contractor_name: Optional[str] = None
    planned_quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    early_start: Optional[datetime] = None
    early_finish: Optional[datetime] = None
    late_start: Optional[datetime] = None
    late_finish: Optional[datetime] = None
    total_float: Optional[float] = None
    free_float: Optional[float] = None
    is_critical: Optional[bool] = None
    driving_predecessor_id: Optional[str] = None
    constraint_type: Optional[str] = None
    constraint_date: Optional[datetime] = None

    @field_validator("percent_complete")
    @classmethod
    def validate_percent(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 100.0):
            raise ValueError("Percentage complete must be between 0 and 100.")
        return v


class ActivityResponse(ActivityBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    wbs_code: Optional[str] = None
    wbs_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ActivityListResponse(BaseModel):
    items: List[ActivityResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
