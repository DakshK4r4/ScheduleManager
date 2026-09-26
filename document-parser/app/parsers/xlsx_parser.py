from __future__ import annotations

import io
from typing import Dict, List
import openpyxl
from app.models.canonical import CanonicalSchedule
from app.parsers.base import BaseParser, ParserError
from app.parsers.tabular_common import build_canonical_schedule_from_rows, map_columns


class XlsxParser(BaseParser):
    def parse(self, content: bytes, filename: str) -> CanonicalSchedule:
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        except Exception as e:
            err_msg = str(e).lower()
            if "does not support the old .xls" in err_msg or filename.lower().endswith(".xls"):
                raise ParserError(
                    f"Legacy Excel (.xls) binary format is not supported for '{filename}'. "
                    "Please convert the file to modern Excel (.xlsx) or CSV (.csv) format and re-upload."
                )
            raise ParserError(f"Failed to open Excel workbook: {str(e)}")

        if not wb.sheetnames:
            raise ParserError("Excel workbook contains no sheets")

        # Pick best sheet (e.g. 'Activities', 'Schedule', 'Tasks', or the active/first sheet)
        selected_sheet = wb.active
        for name in wb.sheetnames:
            if any(k in name.lower() for k in ["activit", "schedule", "task", "p6"]):
                selected_sheet = wb[name]
                break

        if selected_sheet is None:
            selected_sheet = wb.worksheets[0]

        all_rows = list(selected_sheet.iter_rows(values_only=True))
        if not all_rows:
            raise ParserError(f"Worksheet '{selected_sheet.title}' is empty")

        # Find header row (first non-empty row)
        header_row_idx = 0
        headers: List[str] = []
        for idx, row in enumerate(all_rows):
            non_empty = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if len(non_empty) >= 2:
                header_row_idx = idx
                headers = [str(c).strip() if c is not None else "" for c in row]
                break

        if not headers:
            raise ParserError("Could not locate valid header row in Excel worksheet")

        col_map = map_columns(headers)

        dict_rows: List[Dict[str, str]] = []
        for row in all_rows[header_row_idx + 1:]:
            if not any(c is not None and str(c).strip() for c in row):
                continue
            row_dict = {
                headers[i]: str(row[i]).strip() if i < len(row) and row[i] is not None else ""
                for i in range(len(headers))
            }
            dict_rows.append(row_dict)

        return build_canonical_schedule_from_rows(dict_rows, col_map, filename, self)
