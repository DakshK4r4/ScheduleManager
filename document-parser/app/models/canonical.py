from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class ActivityStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class RelationshipType(str, Enum):
    FS = "FS"
    SS = "SS"
    FF = "FF"
    SF = "SF"


class CanonicalProject(BaseModel):
    project_code: str = Field(..., description="Unique project identifier or short name")
    name: str = Field(..., description="Full project name")
    planned_start: Optional[datetime] = Field(None, description="Project planned start datetime")
    planned_finish: Optional[datetime] = Field(None, description="Project planned finish datetime")
    data_date: Optional[datetime] = Field(None, description="Data date / status date")


class CanonicalWBSNode(BaseModel):
    code: str = Field(..., description="Unique WBS code (e.g. WBS.1.1)")
    name: str = Field(..., description="WBS element name or description")
    parent_code: Optional[str] = Field(None, description="Parent WBS code, if applicable")
    wbs_id: Optional[str] = Field(None, description="Optional raw internal WBS ID")


class CanonicalActivity(BaseModel):
    activity_code: str = Field(..., description="Activity ID / Code (e.g. CIV-1001)")
    name: str = Field(..., description="Activity description / name")
    wbs_code: Optional[str] = Field(None, description="Associated WBS code")
    activity_type: Optional[str] = Field("TT_Task", description="Activity type")
    status: ActivityStatus = Field(ActivityStatus.NOT_STARTED, description="Normalized status")
    planned_start: Optional[datetime] = Field(None, description="Planned start datetime")
    planned_finish: Optional[datetime] = Field(None, description="Planned finish datetime")
    actual_start: Optional[datetime] = Field(None, description="Actual start datetime")
    actual_finish: Optional[datetime] = Field(None, description="Actual finish datetime")
    original_duration: Optional[float] = Field(None, description="Original planned duration in days")
    remaining_duration: Optional[float] = Field(None, description="Remaining duration in days")
    percent_complete: Optional[float] = Field(0.0, description="Percentage complete (0 - 100)")
    calendar: Optional[str] = Field(None, description="Calendar name or assignment")
    constraint_type: Optional[str] = Field(None, description="Primary constraint type (e.g. MANDATORY_START, FINISH_NO_LATER)")
    constraint_date: Optional[datetime] = Field(None, description="Primary constraint date")
    activity_codes: Dict[str, str] = Field(default_factory=dict, description="Activity codes map (e.g. {'Discipline': 'Civil'})")
    notes: Optional[str] = Field(None, description="Activity notes or task memo")

    @field_validator("percent_complete")
    @classmethod
    def clamp_percent(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return 0.0
        return max(0.0, min(100.0, float(v)))


class CanonicalRelationship(BaseModel):
    predecessor_code: str = Field(..., description="Predecessor activity code")
    successor_code: str = Field(..., description="Successor activity code")
    relationship_type: RelationshipType = Field(RelationshipType.FS, description="Logic link type")
    lag: Optional[float] = Field(0.0, description="Lag in days")


class CanonicalSchedule(BaseModel):
    project: CanonicalProject
    wbs: List[CanonicalWBSNode] = Field(default_factory=list)
    activities: List[CanonicalActivity] = Field(default_factory=list)
    relationships: List[CanonicalRelationship] = Field(default_factory=list)
