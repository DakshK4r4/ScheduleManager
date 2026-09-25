from __future__ import annotations

import defusedxml.ElementTree as ET
from typing import Dict, List, Optional
from xml.etree.ElementTree import Element

from app.models.canonical import (
    ActivityStatus,
    CanonicalActivity,
    CanonicalProject,
    CanonicalRelationship,
    CanonicalSchedule,
    CanonicalWBSNode,
    RelationshipType,
)
from app.parsers.base import BaseParser, ParserError


class P6XmlParser(BaseParser):
    @staticmethod
    def _clean_tag(tag: str) -> str:
        """Strip XML namespace prefix: '{http://...}Tag' -> 'Tag'."""
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    @classmethod
    def _find_child(cls, elem: Element, tag_name: str) -> Optional[Element]:
        tag_name_lower = tag_name.lower()
        for child in elem:
            if cls._clean_tag(child.tag).lower() == tag_name_lower:
                return child
        return None

    @classmethod
    def _find_text(cls, elem: Element, tag_name: str, default: str = "") -> str:
        child = cls._find_child(elem, tag_name)
        if child is not None and child.text is not None:
            return child.text.strip()
        return default

    @classmethod
    def _find_all_children(cls, root: Element, tag_name: str) -> List[Element]:
        results: List[Element] = []
        target = tag_name.lower()
        for elem in root.iter():
            if cls._clean_tag(elem.tag).lower() == target:
                results.append(elem)
        return results

    def parse(self, content: bytes, filename: str) -> CanonicalSchedule:
        try:
            root = ET.fromstring(content)
        except Exception as e:
            raise ParserError(f"Malformed or invalid XML file: {str(e)}")

        # 1. Parse Project
        project_elems = self._find_all_children(root, "Project")
        if project_elems:
            proj_elem = project_elems[0]
            proj_code = (
                self._find_text(proj_elem, "Id")
                or self._find_text(proj_elem, "ShortName")
                or filename.rsplit(".", 1)[0]
            )
            proj_name = self._find_text(proj_elem, "Name") or proj_code
            plan_start = self.parse_datetime(
                self._find_text(proj_elem, "PlannedStartDate") or self._find_text(proj_elem, "StartDate")
            )
            plan_finish = self.parse_datetime(
                self._find_text(proj_elem, "PlannedFinishDate") or self._find_text(proj_elem, "FinishDate")
            )
            data_date = self.parse_datetime(
                self._find_text(proj_elem, "DataDate") or self._find_text(proj_elem, "LastRecalcDate")
            )
        else:
            proj_code = filename.rsplit(".", 1)[0]
            proj_name = proj_code
            plan_start, plan_finish, data_date = None, None, None

        canonical_project = CanonicalProject(
            project_code=proj_code,
            name=proj_name,
            planned_start=plan_start,
            planned_finish=plan_finish,
            data_date=data_date,
        )

        # 2. Parse WBS
        wbs_elems = self._find_all_children(root, "WBS")
        wbs_obj_to_code: Dict[str, str] = {}
        wbs_obj_to_parent_obj: Dict[str, Optional[str]] = {}
        canonical_wbs_list: List[CanonicalWBSNode] = []

        for w_elem in wbs_elems:
            obj_id = self._find_text(w_elem, "ObjectId")
            code = self._find_text(w_elem, "Code") or self._find_text(w_elem, "ShortName") or f"WBS-{obj_id}"
            parent_id = self._find_text(w_elem, "ParentObjectId") or None
            if obj_id:
                wbs_obj_to_code[obj_id] = code
                wbs_obj_to_parent_obj[obj_id] = parent_id

        for w_elem in wbs_elems:
            obj_id = self._find_text(w_elem, "ObjectId")
            code = wbs_obj_to_code.get(obj_id) or self._find_text(w_elem, "Code") or f"WBS-{obj_id}"
            name = self._find_text(w_elem, "Name") or code
            parent_id = wbs_obj_to_parent_obj.get(obj_id)
            parent_code = wbs_obj_to_code.get(parent_id) if parent_id and parent_id != obj_id else None

            canonical_wbs_list.append(
                CanonicalWBSNode(
                    code=code,
                    name=name,
                    parent_code=parent_code,
                    wbs_id=obj_id,
                )
            )

        # 3. Parse Activities
        act_elems = self._find_all_children(root, "Activity")
        act_obj_to_code: Dict[str, str] = {}
        canonical_activities: List[CanonicalActivity] = []

        for a_elem in act_elems:
            obj_id = self._find_text(a_elem, "ObjectId")
            code = self._find_text(a_elem, "Id") or self._find_text(a_elem, "ActivityId") or f"ACT-{obj_id}"
            if obj_id:
                act_obj_to_code[obj_id] = code

            name = self._find_text(a_elem, "Name") or code
            wbs_obj_id = self._find_text(a_elem, "WBSObjectId")
            wbs_code = wbs_obj_to_code.get(wbs_obj_id) if wbs_obj_id else None

            # Status mapping
            raw_status = self._find_text(a_elem, "Status").lower()
            if "complete" in raw_status:
                status = ActivityStatus.COMPLETED
            elif "progress" in raw_status or "active" in raw_status:
                status = ActivityStatus.IN_PROGRESS
            else:
                status = ActivityStatus.NOT_STARTED

            pct_raw = self.parse_float(self._find_text(a_elem, "PercentComplete"))
            if pct_raw is None:
                pct = 100.0 if status == ActivityStatus.COMPLETED else (50.0 if status == ActivityStatus.IN_PROGRESS else 0.0)
            else:
                pct = pct_raw

            # Durations (if > 100, likely stored in hours; in P6 XML PlannedDuration is hours)
            planned_dur_raw = self.parse_float(self._find_text(a_elem, "PlannedDuration") or self._find_text(a_elem, "OriginalDuration"))
            remain_dur_raw = self.parse_float(self._find_text(a_elem, "RemainingDuration"))

            # Convert hours to days if hours representation
            orig_dur = round(planned_dur_raw / 8.0, 2) if planned_dur_raw is not None else None
            rem_dur = round(remain_dur_raw / 8.0, 2) if remain_dur_raw is not None else orig_dur

            # Constraints
            raw_cstr = (self._find_text(a_elem, "PrimaryConstraintType") or self._find_text(a_elem, "ConstraintType")).strip().lower()
            cstr_type = None
            if "mandatory start" in raw_cstr or "must start on" in raw_cstr:
                cstr_type = "MANDATORY_START"
            elif "mandatory finish" in raw_cstr or "must finish on" in raw_cstr:
                cstr_type = "MANDATORY_FINISH"
            elif "start no earlier" in raw_cstr or "snet" in raw_cstr:
                cstr_type = "START_NO_EARLIER"
            elif "start no later" in raw_cstr or "snlt" in raw_cstr:
                cstr_type = "START_NO_LATER"
            elif "finish no earlier" in raw_cstr or "fnet" in raw_cstr:
                cstr_type = "FINISH_NO_EARLIER"
            elif "finish no later" in raw_cstr or "fnlt" in raw_cstr:
                cstr_type = "FINISH_NO_LATER"
            elif raw_cstr:
                cstr_type = raw_cstr.upper()

            cstr_date = self.parse_datetime(
                self._find_text(a_elem, "PrimaryConstraintDate") or self._find_text(a_elem, "ConstraintDate")
            )

            canonical_activities.append(
                CanonicalActivity(
                    activity_code=code,
                    name=name,
                    wbs_code=wbs_code,
                    activity_type=self._find_text(a_elem, "Type") or "TT_Task",
                    status=status,
                    planned_start=self.parse_datetime(self._find_text(a_elem, "PlannedStartDate") or self._find_text(a_elem, "StartDate")),
                    planned_finish=self.parse_datetime(self._find_text(a_elem, "PlannedFinishDate") or self._find_text(a_elem, "FinishDate")),
                    actual_start=self.parse_datetime(self._find_text(a_elem, "ActualStartDate")),
                    actual_finish=self.parse_datetime(self._find_text(a_elem, "ActualFinishDate")),
                    original_duration=orig_dur,
                    remaining_duration=rem_dur,
                    percent_complete=pct,
                    calendar=self._find_text(a_elem, "CalendarName"),
                    constraint_type=cstr_type,
                    constraint_date=cstr_date,
                )
            )

        # 4. Parse Relationships
        rel_elems = self._find_all_children(root, "Relationship")
        canonical_relationships: List[CanonicalRelationship] = []

        type_map = {
            "finish to start": RelationshipType.FS,
            "fs": RelationshipType.FS,
            "start to start": RelationshipType.SS,
            "ss": RelationshipType.SS,
            "finish to finish": RelationshipType.FF,
            "ff": RelationshipType.FF,
            "start to finish": RelationshipType.SF,
            "sf": RelationshipType.SF,
        }

        for r_elem in rel_elems:
            pred_obj_id = self._find_text(r_elem, "PredecessorActivityObjectId")
            succ_obj_id = self._find_text(r_elem, "SuccessorActivityObjectId")

            # Fallback to direct Activity IDs if ObjectId is absent
            pred_code = act_obj_to_code.get(pred_obj_id) or self._find_text(r_elem, "PredecessorActivityId") or pred_obj_id
            succ_code = act_obj_to_code.get(succ_obj_id) or self._find_text(r_elem, "SuccessorActivityId") or succ_obj_id

            if not pred_code or not succ_code:
                continue

            raw_type = self._find_text(r_elem, "Type").strip().lower()
            rel_type = type_map.get(raw_type, RelationshipType.FS)
            lag_raw = self.parse_float(self._find_text(r_elem, "Lag"), default=0.0)
            # P6 XML lag is in hours; convert to days
            lag_days = round((lag_raw or 0.0) / 8.0, 2)

            canonical_relationships.append(
                CanonicalRelationship(
                    predecessor_code=pred_code,
                    successor_code=succ_code,
                    relationship_type=rel_type,
                    lag=lag_days,
                )
            )

        return CanonicalSchedule(
            project=canonical_project,
            wbs=canonical_wbs_list,
            activities=canonical_activities,
            relationships=canonical_relationships,
        )
