from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.domain.database import get_db
from app.repositories.project_repo import ProjectRepository
from app.schemas.project import ProjectDataDateUpdate, ProjectResponse, ProjectUpdate
from app.services.import_service import ImportService
from app.services.schedule_health_service import ScheduleHealthService
from app.services.scenario_simulation_service import ScenarioSimulationService

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.get("", response_model=List[ProjectResponse], summary="List all projects")
def list_projects(db: Session = Depends(get_db)):
    return ProjectRepository.get_all(db)


@router.get("/{project_id}", response_model=ProjectResponse, summary="Get project by ID")
def get_project(project_id: str, db: Session = Depends(get_db)):
    proj = ProjectRepository.get_by_id(db, project_id)
    if not proj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    return proj


@router.get("/{project_id}/schedule-health", summary="Evaluate DCMA 14-point schedule health and quality metrics")
def get_schedule_health(project_id: str, db: Session = Depends(get_db)):
    result = ScheduleHealthService.evaluate_project_health(db=db, project_id=project_id)
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["error"],
        )
    return result


class ScenarioChangePayload(BaseModel):
    activity_code: str
    delay_days: Optional[float] = 0.0
    delay_category: Optional[str] = "Unknown"
    notes: Optional[str] = None
    constraint_type: Optional[str] = None
    constraint_date: Optional[datetime] = None
    status: Optional[str] = None


class ScenarioSimulationPayload(BaseModel):
    changes: List[ScenarioChangePayload]


@router.post("/{project_id}/simulate", summary="Run what-if scenario delay simulation without mutating master schedule")
def simulate_project_scenario(
    project_id: str,
    payload: ScenarioSimulationPayload,
    db: Session = Depends(get_db),
):
    project = ProjectRepository.get_by_id(db, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    return ScenarioSimulationService.simulate_scenario(
        db=db,
        project=project,
        changes=[c.model_dump() for c in payload.changes],
    )


@router.patch("/{project_id}", response_model=ProjectResponse, summary="Update project metadata")
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
):
    updates = payload.model_dump(exclude_unset=True)
    updated = ProjectRepository.update(db, project_id, updates)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    return updated


@router.patch("/{project_id}/data-date", response_model=ProjectResponse, summary="Set or update project Data Date")
def update_project_data_date(
    project_id: str,
    payload: ProjectDataDateUpdate,
    db: Session = Depends(get_db),
):
    updated = ProjectRepository.update(db, project_id, {"data_date": payload.data_date})
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    return updated


@router.post("/import", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, summary="Import schedule file")
async def import_schedule(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    filename = file.filename or "uploaded_schedule"
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to read upload: {str(e)}")

    if not content:
        raise HTTPException(status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY), detail="Uploaded file is empty.")

    return await ImportService.import_schedule_file(
        db=db,
        filename=filename,
        content=content,
        content_type=file.content_type,
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete project")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    deleted = ProjectRepository.delete(db, project_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    return None
