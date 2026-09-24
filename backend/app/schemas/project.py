from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ProjectBase(BaseModel):
    project_code: str = Field(..., description="Unique project short code")
    name: str = Field(..., description="Project full name")
    planned_start: Optional[datetime] = None
    planned_finish: Optional[datetime] = None
    data_date: Optional[datetime] = None


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    planned_start: Optional[datetime] = None
    planned_finish: Optional[datetime] = None
    data_date: Optional[datetime] = None


class ProjectDataDateUpdate(BaseModel):
    data_date: Optional[datetime] = Field(None, description="Project progress cutoff data date")


class ProjectResponse(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime
    activity_count: int = 0
    wbs_count: int = 0
    relationship_count: int = 0
