from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from app.schemas.validation import ValidationErrorDetail, ValidationResponse


class ValidationException(Exception):
    def __init__(self, errors: Any, message: Optional[str] = None):
        if isinstance(errors, str):
            msg = errors
            errs = [errors]
        else:
            msg = message or "Schedule validation failed"
            errs = errors
        super().__init__(msg)
        self.message = msg
        self.errors = errs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.message,
            "errors": [err.model_dump() for err in self.errors],
        }


class ValidationService:
    @staticmethod
    def validate_canonical_schedule(data: Dict[str, Any]) -> List[ValidationErrorDetail]:
        errors: List[ValidationErrorDetail] = []

        # 1. Project validation
        project = data.get("project")
        if not project:
            errors.append(
                ValidationErrorDetail(
                    field="project",
                    message="Project metadata is required.",
                    code="REQUIRED_PROJECT",
                )
            )
        else:
            if not project.get("project_code"):
                errors.append(
                    ValidationErrorDetail(
                        field="project.project_code",
                        message="Project code cannot be empty.",
                        code="REQUIRED_PROJECT_CODE",
                    )
                )
            if not project.get("name"):
                errors.append(
                    ValidationErrorDetail(
                        field="project.name",
                        message="Project name cannot be empty.",
                        code="REQUIRED_PROJECT_NAME",
                    )
                )

        # 2. WBS validation
        wbs_list = data.get("wbs", [])
        wbs_codes: Set[str] = set()
        for idx, wbs in enumerate(wbs_list):
            code = wbs.get("code")
            if not code:
                errors.append(
                    ValidationErrorDetail(
                        field=f"wbs[{idx}].code",
                        message="WBS node code cannot be empty.",
                        code="EMPTY_WBS_CODE",
                    )
                )
            else:
                if code in wbs_codes:
                    errors.append(
                        ValidationErrorDetail(
                            field=f"wbs[{idx}].code",
                            message=f"Duplicate WBS code '{code}'.",
                            code="DUPLICATE_WBS_CODE",
                            value=code,
                        )
                    )
                wbs_codes.add(code)

        # Validate parent WBS references
        for idx, wbs in enumerate(wbs_list):
            parent = wbs.get("parent_code")
            if parent and parent not in wbs_codes:
                errors.append(
                    ValidationErrorDetail(
                        field=f"wbs[{idx}].parent_code",
                        message=f"Referenced parent WBS '{parent}' does not exist in schedule.",
                        code="MISSING_PARENT_WBS",
                        value=parent,
                    )
                )

        # 3. Activities validation
        activities = data.get("activities", [])
        if not activities:
            errors.append(
                ValidationErrorDetail(
                    field="activities",
                    message="Schedule must contain at least one activity.",
                    code="EMPTY_ACTIVITIES",
                )
            )

        activity_codes: Set[str] = set()
        for idx, act in enumerate(activities):
            code = act.get("activity_code")
            if not code:
                errors.append(
                    ValidationErrorDetail(
                        field=f"activities[{idx}].activity_code",
                        message="Activity code is required.",
                        code="REQUIRED_ACTIVITY_CODE",
                    )
                )
            else:
                if code in activity_codes:
                    errors.append(
                        ValidationErrorDetail(
                            field=f"activities[{idx}].activity_code",
                            message=f"Duplicate activity code '{code}' detected in schedule.",
                            code="DUPLICATE_ACTIVITY_CODE",
                            value=code,
                        )
                    )
                activity_codes.add(code)

            # Name check
            if not act.get("name"):
                errors.append(
                    ValidationErrorDetail(
                        field=f"activities[{idx}].name",
                        message=f"Activity '{code}' is missing a name/description.",
                        code="REQUIRED_ACTIVITY_NAME",
                    )
                )

            # WBS reference check
            wbs_code = act.get("wbs_code")
            if wbs_code and wbs_codes and wbs_code not in wbs_codes:
                errors.append(
                    ValidationErrorDetail(
                        field=f"activities[{idx}].wbs_code",
                        message=f"Referenced WBS '{wbs_code}' does not exist in project WBS.",
                        code="INVALID_WBS_REFERENCE",
                        value=wbs_code,
                    )
                )

            # Percent complete check
            pct = act.get("percent_complete")
            if pct is not None:
                try:
                    p_val = float(pct)
                    if p_val < 0.0 or p_val > 100.0:
                        errors.append(
                            ValidationErrorDetail(
                                field=f"activities[{idx}].percent_complete",
                                message=f"Percent complete for '{code}' must be between 0 and 100 (got {p_val}).",
                                code="INVALID_PERCENT_COMPLETE",
                                value=p_val,
                            )
                        )
                except (ValueError, TypeError):
                    errors.append(
                        ValidationErrorDetail(
                            field=f"activities[{idx}].percent_complete",
                            message=f"Invalid percentage complete format '{pct}'.",
                            code="MALFORMED_PERCENT_COMPLETE",
                        )
                    )

            # Date consistency check
            start = act.get("planned_start")
            finish = act.get("planned_finish")
            if start and finish:
                try:
                    if str(finish) < str(start):
                        errors.append(
                            ValidationErrorDetail(
                                field=f"activities[{idx}].planned_finish",
                                message=f"Planned finish date ({finish}) cannot precede planned start date ({start}) for activity '{code}'.",
                                code="FINISH_BEFORE_START",
                                value=finish,
                            )
                        )
                except Exception:
                    pass

        # 4. Relationships validation
        relationships = data.get("relationships", [])
        valid_rel_types = {"FS", "SS", "FF", "SF"}
        seen_edges: Set[tuple] = set()

        for idx, rel in enumerate(relationships):
            pred = rel.get("predecessor_code")
            succ = rel.get("successor_code")
            rel_type = (rel.get("relationship_type") or "FS").upper()

            if not pred:
                errors.append(
                    ValidationErrorDetail(
                        field=f"relationships[{idx}].predecessor_code",
                        message="Relationship missing predecessor activity code.",
                        code="REQUIRED_PREDECESSOR",
                        severity="ERROR",
                    )
                )
            elif pred not in activity_codes:
                errors.append(
                    ValidationErrorDetail(
                        field=f"relationships[{idx}].predecessor_code",
                        message=f"Relationship references external/unresolved predecessor activity '{pred}'. Skipped during single-project import.",
                        code="EXTERNAL_PREDECESSOR",
                        value=pred,
                        severity="WARNING",
                    )
                )

            if not succ:
                errors.append(
                    ValidationErrorDetail(
                        field=f"relationships[{idx}].successor_code",
                        message="Relationship missing successor activity code.",
                        code="REQUIRED_SUCCESSOR",
                        severity="ERROR",
                    )
                )
            elif succ not in activity_codes:
                errors.append(
                    ValidationErrorDetail(
                        field=f"relationships[{idx}].successor_code",
                        message=f"Relationship references external/unresolved successor activity '{succ}'. Skipped during single-project import.",
                        code="EXTERNAL_SUCCESSOR",
                        value=succ,
                        severity="WARNING",
                    )
                )

            # Self-reference check
            if pred and succ and pred == succ:
                errors.append(
                    ValidationErrorDetail(
                        field=f"relationships[{idx}]",
                        message=f"Activity '{pred}' cannot have a relationship with itself.",
                        code="SELF_REFERENCING_RELATIONSHIP",
                        value=pred,
                    )
                )

            # Valid type check
            if rel_type not in valid_rel_types:
                errors.append(
                    ValidationErrorDetail(
                        field=f"relationships[{idx}].relationship_type",
                        message=f"Invalid relationship type '{rel_type}'. Must be one of: {', '.join(valid_rel_types)}.",
                        code="INVALID_RELATIONSHIP_TYPE",
                        value=rel_type,
                    )
                )

            edge_key = (pred, succ, rel_type)
            if edge_key in seen_edges:
                errors.append(
                    ValidationErrorDetail(
                        field=f"relationships[{idx}]",
                        message=f"Duplicate relationship between '{pred}' and '{succ}' ({rel_type}).",
                        code="DUPLICATE_RELATIONSHIP",
                    )
                )
            seen_edges.add(edge_key)

        return errors

    @staticmethod
    def validate_activity_update(updates: Dict[str, Any], current_start: Optional[Any], current_finish: Optional[Any]):
        errors: List[ValidationErrorDetail] = []

        new_start = updates.get("planned_start", current_start)
        new_finish = updates.get("planned_finish", current_finish)

        if new_start and new_finish and str(new_finish) < str(new_start):
            errors.append(
                ValidationErrorDetail(
                    field="planned_finish",
                    message=f"Planned finish ({new_finish}) cannot precede planned start ({new_start}).",
                    code="FINISH_BEFORE_START",
                )
            )

        pct = updates.get("percent_complete")
        if pct is not None:
            try:
                p_val = float(pct)
                if not (0.0 <= p_val <= 100.0):
                    errors.append(
                        ValidationErrorDetail(
                            field="percent_complete",
                            message="Percent complete must be between 0 and 100.",
                            code="INVALID_PERCENT_COMPLETE",
                            value=p_val,
                        )
                    )
            except (ValueError, TypeError):
                errors.append(
                    ValidationErrorDetail(
                        field="percent_complete",
                        message="Percent complete must be a valid number.",
                        code="MALFORMED_PERCENT_COMPLETE",
                    )
                )

        if errors:
            raise ValidationException(errors)
