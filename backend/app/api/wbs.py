from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.validation import validate_project_id
from app.domain.database import get_db
from app.repositories.project_repo import ProjectRepository
from app.repositories.wbs_repo import WBSRepository
from app.schemas.wbs import WBSNodeResponse, WBSTreeNode

router = APIRouter(tags=["WBS"])


@router.get(
    "/projects/{project_id}/wbs",
    response_model=List[WBSNodeResponse],
    summary="Get flat list of WBS nodes for a project",
)
def get_project_wbs(project_id: str, db: Session = Depends(get_db)):
    clean_id = validate_project_id(project_id)
    proj = ProjectRepository.get_by_id(db, clean_id)
    if not proj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return WBSRepository.get_by_project(db, clean_id)


@router.get(
    "/projects/{project_id}/wbs/tree",
    response_model=List[WBSTreeNode],
    summary="Get hierarchical tree of WBS nodes for a project",
)
def get_project_wbs_tree(project_id: str, db: Session = Depends(get_db)):
    clean_id = validate_project_id(project_id)
    proj = ProjectRepository.get_by_id(db, clean_id)
    if not proj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return WBSRepository.get_tree(db, clean_id)
