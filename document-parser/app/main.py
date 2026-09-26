from __future__ import annotations

import logging
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.models.canonical import CanonicalSchedule
from app.parsers import (
    BaseParser,
    CsvParser,
    P6XmlParser,
    ParserError,
    XerParser,
    XlsxParser,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("document-parser")

app = FastAPI(
    title="Primavera Document Parser Service",
    description="Independent service for parsing and normalizing Primavera .xer, .xml, .csv, and .xlsx files into a canonical schedule model.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://.*$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "document-parser"}


def detect_parser(filename: str, content: bytes) -> BaseParser:
    lower_name = filename.lower()
    header_preview = content[:2048]

    if lower_name.endswith(".xer"):
        return XerParser()
    elif lower_name.endswith(".xml"):
        return P6XmlParser()
    elif lower_name.endswith(".xlsx") or lower_name.endswith(".xls"):
        return XlsxParser()
    elif lower_name.endswith(".csv") or lower_name.endswith(".tsv"):
        return CsvParser()

    # Content sniffing fallback if extension is generic
    if b"ERMHDR" in header_preview or b"%T\t" in header_preview:
        return XerParser()
    elif b"<?xml" in header_preview or b"<Project" in header_preview or b"<WBS" in header_preview:
        return P6XmlParser()
    elif header_preview.startswith(b"PK\x03\x04"):  # ZIP/XLSX header
        return XlsxParser()
    elif b"," in header_preview or b"\t" in header_preview:
        return CsvParser()

    # Explicit check for Microsoft Project .mpp binary format
    if lower_name.endswith(".mpp") or (content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and b"MSProject" in content[:4096]):
        raise ParserError(
            f"Microsoft Project proprietary binary format (.mpp) is not supported for '{filename}'. "
            "Please export your schedule from Microsoft Project as XML (.xml) or Excel (.xlsx) format and re-upload."
        )

    raise ParserError(
        f"Unsupported or unrecognized schedule file format for '{filename}'. "
        "Supported extensions: .xer, .xml, .csv, .xlsx."
    )


@app.post(
    "/parse",
    response_model=CanonicalSchedule,
    summary="Parse a schedule file into canonical JSON",
    tags=["Parser"],
)
async def parse_schedule_file(file: UploadFile = File(...)):
    filename = file.filename or "uploaded_schedule"
    logger.info(f"Parsing uploaded file: {filename}")

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded file: {str(e)}",
        )

    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The uploaded file is empty (0 bytes).",
        )

    try:
        parser = detect_parser(filename, content)
        canonical_schedule = parser.parse(content, filename)
        logger.info(
            f"Successfully parsed {filename}: "
            f"{len(canonical_schedule.activities)} activities, "
            f"{len(canonical_schedule.wbs)} WBS nodes, "
            f"{len(canonical_schedule.relationships)} relationships."
        )
        return canonical_schedule
    except ParserError as pe:
        logger.warning(f"Parser error while parsing {filename}: {pe.message}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "Parser failed",
                "message": pe.message,
                "field": pe.field,
                "details": pe.details,
            },
        )
    except Exception as e:
        logger.exception(f"Unexpected error while parsing {filename}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal parser error",
                "message": f"An unexpected error occurred: {str(e)}",
            },
        )
