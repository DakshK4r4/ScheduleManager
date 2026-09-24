from __future__ import annotations

from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityRelationship, Project, WBSNode
from app.schemas.project import ProjectResponse


class ProjectRepository:
    @staticmethod
    def get_all(db: Session) -> List[ProjectResponse]:
        projects = db.query(Project).order_by(Project.created_at.desc()).all()
        results = []
        for p in projects:
            act_count = db.query(func.count(Activity.id)).filter(Activity.project_id == p.id).scalar() or 0
            wbs_count = db.query(func.count(WBSNode.id)).filter(WBSNode.project_id == p.id).scalar() or 0
            rel_count = db.query(func.count(ActivityRelationship.id)).filter(ActivityRelationship.project_id == p.id).scalar() or 0

            results.append(
                ProjectResponse(
                    id=p.id,
                    project_code=p.project_code,
                    name=p.name,
                    planned_start=p.planned_start,
                    planned_finish=p.planned_finish,
                    data_date=p.data_date,
                    created_at=p.created_at,
                    updated_at=p.updated_at,
                    activity_count=act_count,
                    wbs_count=wbs_count,
                    relationship_count=rel_count,
                )
            )
        return results

    @staticmethod
    def get_by_id(db: Session, project_id: str) -> Optional[ProjectResponse]:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            return None
        act_count = db.query(func.count(Activity.id)).filter(Activity.project_id == p.id).scalar() or 0
        wbs_count = db.query(func.count(WBSNode.id)).filter(WBSNode.project_id == p.id).scalar() or 0
        rel_count = db.query(func.count(ActivityRelationship.id)).filter(ActivityRelationship.project_id == p.id).scalar() or 0

        return ProjectResponse(
            id=p.id,
            project_code=p.project_code,
            name=p.name,
            planned_start=p.planned_start,
            planned_finish=p.planned_finish,
            data_date=p.data_date,
            created_at=p.created_at,
            updated_at=p.updated_at,
            activity_count=act_count,
            wbs_count=wbs_count,
            relationship_count=rel_count,
        )

    @staticmethod
    def get_by_code(db: Session, code: str) -> Optional[Project]:
        return db.query(Project).filter(Project.project_code == code).first()

    @staticmethod
    def create(db: Session, project: Project) -> Project:
        db.add(project)
        db.flush()
        return project

    @staticmethod
    def update(db: Session, project_id: str, updates: dict) -> Optional[ProjectResponse]:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            return None
        for key, val in updates.items():
            if hasattr(p, key) and key not in ("id", "project_code", "created_at"):
                setattr(p, key, val)
        db.commit()
        db.refresh(p)
        return ProjectRepository.get_by_id(db, project_id)

    @staticmethod
    def delete(db: Session, project_id: str) -> bool:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return False
        db.delete(project)
        db.commit()
        return True
