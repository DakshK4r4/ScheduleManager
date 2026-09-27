from __future__ import annotations

import re
from fastapi import HTTPException, status

INVALID_ID_LITERALS = {
    "undefined",
    "null",
    "none",
    "nan",
    "[object object]",
    "false",
}


def validate_project_id(project_id: str) -> str:
    """
    Validate that project_id is a non-empty, well-formed identifier.
    Returns the sanitized/stripped project ID.
    Raises HTTPException 400 for invalid/malformed IDs so that invalid route
    requests are never erroneously reported as genuine 404 'Project Not Found'.
    """
    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project ID must not be empty.",
        )
    cleaned = project_id.strip()
    if not cleaned or cleaned.lower() in INVALID_ID_LITERALS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid project ID format: '{project_id}'.",
        )
    # Check length and acceptable characters (UUIDs, slug IDs, alphanumeric, hyphen, underscore)
    if len(cleaned) > 100 or not re.match(r"^[A-Za-z0-9_\-]+$", cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed project ID: '{project_id}'. Must contain only alphanumeric characters, hyphens, or underscores.",
        )
    return cleaned
