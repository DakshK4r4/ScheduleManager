from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityRelationship, Project, WBSNode
from app.repositories.project_repo import ProjectRepository
from app.schemas.project import ProjectResponse
from app.schemas.validation import ValidationErrorDetail
from app.services.validation_service import ValidationException, ValidationService

logger = logging.getLogger("import-service")
PARSER_URL = os.getenv("PARSER_URL", "http://document-parser:8001")


class ImportService:
    @staticmethod
    async def import_schedule_file(
        db: Session,
        filename: str,
        content: bytes,
        content_type: Optional[str] = None,
    ) -> ProjectResponse:
        # 1. Forward file to document-parser service
        logger.info(f"Forwarding '{filename}' to document-parser at {PARSER_URL}/parse")
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.post(
                    f"{PARSER_URL}/parse",
                    files={"file": (filename, content, content_type or "application/octet-stream")},
                )
            except httpx.RequestError as exc:
                raise ValidationException(
                    [
                        ValidationErrorDetail(
                            field="parser",
                            message=f"Could not connect to document parser service at {PARSER_URL}: {str(exc)}",
                            code="PARSER_UNAVAILABLE",
                        )
                    ],
                    message="Document parser service is unavailable.",
                )

        if response.status_code != 200:
            try:
                err_data = response.json()
                msg = err_data.get("message") or err_data.get("error") or response.text
                field = err_data.get("field")
            except Exception:
                msg = response.text
                field = None

            raise ValidationException(
                [
                    ValidationErrorDetail(
                        field=field or "file",
                        message=msg,
                        code="PARSER_ERROR",
                    )
                ],
                message="Failed to parse schedule document.",
            )

        canonical_data = response.json()

        # 2. Run backend validation rules
        validation_errors = ValidationService.validate_canonical_schedule(canonical_data)
        fatal_errors = [e for e in validation_errors if getattr(e, "severity", "ERROR") == "ERROR"]
        warnings = [e for e in validation_errors if getattr(e, "severity", "ERROR") == "WARNING"]
        if warnings:
            logger.info(f"Schedule '{filename}' contains {len(warnings)} non-fatal warning(s)")
        if fatal_errors:
            logger.warning(f"Validation failed for '{filename}' with {len(fatal_errors)} fatal error(s)")
            raise ValidationException(fatal_errors)

        # 3. Transactional atomic insertion into PostgreSQL
        try:
            proj_data = canonical_data["project"]
            proj_id = str(uuid.uuid4())

            def parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
                if not dt_str:
                    return None
                try:
                    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                except Exception:
                    return None

            new_project = Project(
                id=proj_id,
                project_code=proj_data.get("project_code") or filename.rsplit(".", 1)[0],
                name=proj_data.get("name") or proj_data.get("project_code") or "Imported Project",
                planned_start=parse_dt(proj_data.get("planned_start")),
                planned_finish=parse_dt(proj_data.get("planned_finish")),
                data_date=parse_dt(proj_data.get("data_date")),
            )
            db.add(new_project)
            db.flush()

            # Insert WBS nodes
            wbs_code_to_id: Dict[str, str] = {}
            wbs_items = canonical_data.get("wbs", [])

            # First pass: create all WBS objects with UUIDs
            wbs_models: Dict[str, WBSNode] = {}
            for w in wbs_items:
                w_id = str(uuid.uuid4())
                w_code = w["code"]
                wbs_code_to_id[w_code] = w_id
                wbs_node = WBSNode(
                    id=w_id,
                    project_id=proj_id,
                    code=w_code,
                    name=w.get("name") or w_code,
                    parent_id=None,
                )
                wbs_models[w_code] = wbs_node
                db.add(wbs_node)

            db.flush()

            # Second pass: link parents
            for w in wbs_items:
                parent_code = w.get("parent_code")
                if parent_code and parent_code in wbs_code_to_id:
                    wbs_models[w["code"]].parent_id = wbs_code_to_id[parent_code]

            db.flush()

            # Insert Activities
            act_code_to_id: Dict[str, str] = {}
            for a in canonical_data.get("activities", []):
                act_id = str(uuid.uuid4())
                act_code = a["activity_code"]
                act_code_to_id[act_code] = act_id
                wbs_code = a.get("wbs_code")
                wbs_id = wbs_code_to_id.get(wbs_code) if wbs_code else None

                act_codes = a.get("activity_codes") or {}
                activity = Activity(
                    id=act_id,
                    project_id=proj_id,
                    wbs_id=wbs_id,
                    activity_code=act_code,
                    name=a.get("name") or act_code,
                    activity_type=a.get("activity_type") or "TT_Task",
                    status=(a.get("status") or "NOT_STARTED").upper(),
                    planned_start=parse_dt(a.get("planned_start")),
                    planned_finish=parse_dt(a.get("planned_finish")),
                    actual_start=parse_dt(a.get("actual_start")),
                    actual_finish=parse_dt(a.get("actual_finish")),
                    original_duration=a.get("original_duration"),
                    remaining_duration=a.get("remaining_duration"),
                    percent_complete=a.get("percent_complete") or 0.0,
                    calendar=a.get("calendar"),
                    constraint_type=a.get("constraint_type"),
                    constraint_date=parse_dt(a.get("constraint_date")),
                    activity_codes=act_codes if act_codes else None,
                    discipline=a.get("discipline") or act_codes.get("Discipline") or act_codes.get("DISCIPLINE"),
                    location_code=a.get("location_code") or act_codes.get("Location") or act_codes.get("LOCATION") or act_codes.get("Area") or act_codes.get("AREA"),
                    contractor_name=a.get("contractor_name") or act_codes.get("Contractor") or act_codes.get("CONTRACTOR"),
                    notes=a.get("notes"),
                )
                db.add(activity)

            db.flush()

            # Insert Relationships
            for r in canonical_data.get("relationships", []):
                pred_code = r.get("predecessor_code")
                succ_code = r.get("successor_code")
                pred_id = act_code_to_id.get(pred_code)
                succ_id = act_code_to_id.get(succ_code)

                if pred_id and succ_id:
                    rel = ActivityRelationship(
                        id=str(uuid.uuid4()),
                        project_id=proj_id,
                        predecessor_id=pred_id,
                        successor_id=succ_id,
                        relationship_type=(r.get("relationship_type") or "FS").upper(),
                        lag=float(r.get("lag") or 0.0),
                    )
                    db.add(rel)

            db.commit()
            logger.info(f"Successfully committed project '{new_project.project_code}' ({proj_id}) to PostgreSQL")
            return ProjectRepository.get_by_id(db, proj_id)

        except Exception as e:
            db.rollback()
            logger.exception("Database transaction failed during schedule import, rolled back")
            raise e
