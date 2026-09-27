from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.validation import validate_project_id
from app.domain.database import get_db
from app.domain.models import ActivityRelationship
from app.repositories.activity_repo import ActivityRepository
from app.repositories.project_repo import ProjectRepository
from app.repositories.relationship_repo import RelationshipRepository
from app.schemas.relationship import (
    RelationshipCreate,
    RelationshipResponse,
    RelationshipUpdate,
)

router = APIRouter(tags=["Relationships"])


@router.get(
    "/projects/{project_id}/relationships",
    response_model=List[RelationshipResponse],
    summary="Get all relationships for a project",
)
def get_project_relationships(project_id: str, db: Session = Depends(get_db)):
    clean_id = validate_project_id(project_id)
    proj = ProjectRepository.get_by_id(db, clean_id)
    if not proj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return RelationshipRepository.get_by_project(db, clean_id)


@router.post(
    "/projects/{project_id}/relationships",
    response_model=RelationshipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new relationship between activities",
)
def create_relationship(
    project_id: str,
    payload: RelationshipCreate,
    db: Session = Depends(get_db),
):
    clean_id = validate_project_id(project_id)
    proj = ProjectRepository.get_by_id(db, clean_id)
    if not proj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")

    # Prevent self-referencing relationship
    if payload.predecessor_id == payload.successor_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An activity cannot have a relationship with itself.",
        )

    # Validate predecessor
    pred = ActivityRepository.get_by_id(db, payload.predecessor_id)
    if not pred or pred.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Predecessor activity does not exist in this project.",
        )

    # Validate successor
    succ = ActivityRepository.get_by_id(db, payload.successor_id)
    if not succ or succ.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Successor activity does not exist in this project.",
        )

    # Check for duplicate
    existing = (
        db.query(ActivityRelationship)
        .filter(
            ActivityRelationship.predecessor_id == payload.predecessor_id,
            ActivityRelationship.successor_id == payload.successor_id,
            ActivityRelationship.relationship_type == payload.relationship_type.upper(),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Relationship between '{pred.activity_code}' and '{succ.activity_code}' of type '{payload.relationship_type}' already exists.",
        )

    rel = ActivityRelationship(
        project_id=project_id,
        predecessor_id=payload.predecessor_id,
        successor_id=payload.successor_id,
        relationship_type=payload.relationship_type.upper(),
        lag=payload.lag,
    )
    db.add(rel)
    db.commit()
    db.refresh(rel)

    return RelationshipResponse(
        id=rel.id,
        project_id=rel.project_id,
        predecessor_id=rel.predecessor_id,
        successor_id=rel.successor_id,
        predecessor_code=pred.activity_code,
        predecessor_name=pred.name,
        successor_code=succ.activity_code,
        successor_name=succ.name,
        relationship_type=rel.relationship_type,
        lag=rel.lag,
        created_at=rel.created_at,
    )


@router.patch(
    "/relationships/{relationship_id}",
    response_model=RelationshipResponse,
    summary="Update relationship type or lag",
)
def update_relationship(
    relationship_id: str,
    payload: RelationshipUpdate,
    db: Session = Depends(get_db),
):
    rel = RelationshipRepository.get_by_id(db, relationship_id)
    if not rel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found.")

    updates = payload.model_dump(exclude_unset=True)
    RelationshipRepository.update(db, rel, updates)
    db.commit()
    db.refresh(rel)

    pred = ActivityRepository.get_by_id(db, rel.predecessor_id)
    succ = ActivityRepository.get_by_id(db, rel.successor_id)

    return RelationshipResponse(
        id=rel.id,
        project_id=rel.project_id,
        predecessor_id=rel.predecessor_id,
        successor_id=rel.successor_id,
        predecessor_code=pred.activity_code if pred else "",
        predecessor_name=pred.name if pred else "",
        successor_code=succ.activity_code if succ else "",
        successor_name=succ.name if succ else "",
        relationship_type=rel.relationship_type,
        lag=rel.lag,
        created_at=rel.created_at,
    )


@router.delete(
    "/relationships/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a relationship",
)
def delete_relationship(relationship_id: str, db: Session = Depends(get_db)):
    deleted = RelationshipRepository.delete(db, relationship_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found.")
    return None
