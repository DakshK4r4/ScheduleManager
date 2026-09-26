from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel


class ValidationErrorDetail(BaseModel):
    field: Optional[str] = None
    message: str
    code: Optional[str] = None
    value: Optional[Any] = None
    severity: str = "ERROR"  # "ERROR" or "WARNING"


class ValidationResponse(BaseModel):
    error: str = "Schedule validation failed"
    errors: List[ValidationErrorDetail]
