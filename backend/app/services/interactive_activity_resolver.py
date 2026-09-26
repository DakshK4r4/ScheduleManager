from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.models import Activity, ExecutionEvent, Project, WBSNode
from app.schemas.agent import ActionCardDTO
from app.services.activity_reference_resolver import ActivityReferenceResolver

logger = logging.getLogger("interactive_activity_resolver")


class ResolutionSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class ResolutionStepResult:
    status: ResolutionSessionStatus
    resolved_activity: Optional[Activity] = None
    question_text: Optional[str] = None
    action_card: Optional[ActionCardDTO] = None
    remaining_candidates: List[Activity] = field(default_factory=list)
    eliminated_candidates: List[Activity] = field(default_factory=list)
    audit_message: str = ""
    round_number: int = 1


@dataclass
class ActivityResolutionSession:
    """
    Explicit state representing an in-flight, multi-turn activity disambiguation session.
    Strictly scoped to a single project and conversation.
    Survives HTTP request/response cycles by serializing into execution event metadata.
    """
    session_id: str
    project_id: str
    conversation_id: str
    original_user_reference: str
    candidate_activity_ids: List[str]
    extracted_clues: Dict[str, Any] = field(default_factory=dict)
    candidate_options_presented: List[Dict[str, Any]] = field(default_factory=list)
    asked_questions: List[Dict[str, Any]] = field(default_factory=list)
    current_question: Optional[Dict[str, Any]] = None
    round_number: int = 1
    max_rounds: int = 3
    status: str = ResolutionSessionStatus.ACTIVE.value
    resolved_activity_id: Optional[str] = None
    history_log: List[Dict[str, Any]] = field(default_factory=list)
    partially_resolved_updates: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActivityResolutionSession:
        return cls(
            session_id=data.get("session_id") or f"ars-{uuid.uuid4().hex[:8]}",
            project_id=str(data["project_id"]),
            conversation_id=str(data["conversation_id"]),
            original_user_reference=data.get("original_user_reference", ""),
            candidate_activity_ids=[str(cid) for cid in data.get("candidate_activity_ids", [])],
            extracted_clues=dict(data.get("extracted_clues") or {}),
            candidate_options_presented=list(data.get("candidate_options_presented") or []),
            asked_questions=list(data.get("asked_questions") or []),
            current_question=data.get("current_question"),
            round_number=int(data.get("round_number", 1)),
            max_rounds=int(data.get("max_rounds", 3)),
            status=str(data.get("status", ResolutionSessionStatus.ACTIVE.value)),
            resolved_activity_id=data.get("resolved_activity_id"),
            history_log=list(data.get("history_log") or []),
            partially_resolved_updates=list(data.get("partially_resolved_updates") or []),
        )


class InteractiveActivityResolver:
    """
    Progressive Activity Disambiguation / Interactive Activity Resolver.
    
    Principles:
      1. Deterministic candidate narrowing (Akinator-style information gain).
      2. Strictly operates within the active project's candidate set.
      3. Ambiguity never implies bulk update.
      4. Explicit percentage and reported quantities remain anchored throughout resolution.
      5. Ordinals ('second one', '2nd', '2') resolve strictly against immediate prior presented list.
      6. Yes/No answers only resolve activity identity; NEVER confirm schedule mutation.
      7. Hard bound of max_rounds (default 3) prevents infinite interrogation loops.
    """

    ORDINAL_MAP = {
        "first": 1, "first one": 1, "1st": 1, "1": 1, "#1": 1, "one": 1,
        "pehla": 1, "pehli": 1, "pehle wala": 1, "pehla wala": 1, "pehle": 1,
        "second": 2, "second one": 2, "2nd": 2, "2": 2, "#2": 2, "two": 2,
        "doosra": 2, "doosri": 2, "doosre wala": 2, "doosra wala": 2, "dusra": 2,
        "third": 3, "third one": 3, "3rd": 3, "3": 3, "#3": 3, "three": 3,
        "teesra": 3, "teesri": 3, "teesra wala": 3, "tisra": 3,
        "fourth": 4, "fourth one": 4, "4th": 4, "4": 4, "#4": 4, "four": 4,
        "chautha": 4, "chauthi": 4,
        "fifth": 5, "fifth one": 5, "5th": 5, "5": 5, "#5": 5, "five": 5,
        "paanchwa": 5, "panchwa": 5,
        "sixth": 6, "6th": 6, "6": 6, "#6": 6,
    }

    AFFIRMATIVE_WORDS = {
        "yes", "yeah", "yep", "yup", "sure", "correct", "right", "true", "haan",
        "ha", "theek", "sahi", "sahi hai", "bilkul", "yes please", "ji haan", "ji",
    }

    NEGATIVE_WORDS = {
        "no", "nope", "nah", "not this", "not this one", "nahi", "nahin", "galat",
        "ye nahi", "yeh nahi", "na",
    }

    CANCELLATION_WORDS = {
        "cancel", "stop", "abort", "dismiss", "exit", "quit", "leave",
        "radd karo", "band karo", "cancel karo", "nahi karna",
    }

    NONE_OF_THESE_WORDS = {
        "none_of_these", "none of these", "neither", "none", "no of these",
        "inme se koi nahi", "koi nahi", "none of them",
    }

    @classmethod
    def start_session(
        cls,
        project_id: str,
        conversation_id: str,
        original_reference: str,
        candidate_activities: List[Activity],
        extracted_clues: Dict[str, Any],
        partially_resolved_updates: Optional[List[Dict[str, Any]]] = None,
        resp_lang: str = "en",
    ) -> Tuple[ActivityResolutionSession, ResolutionStepResult]:
        """
        Initializes an explicit resolution session and determines the first optimal distinguishing question.
        Ensures all candidates belong strictly to project_id.
        """
        # Defense-in-depth: cross-project candidate rejection
        safe_candidates = [
            a for a in candidate_activities
            if str(a.project_id) == str(project_id)
        ]
        candidate_ids = [a.id for a in safe_candidates]

        session = ActivityResolutionSession(
            session_id=f"ars-{uuid.uuid4().hex[:8]}",
            project_id=str(project_id),
            conversation_id=str(conversation_id),
            original_user_reference=original_reference,
            candidate_activity_ids=candidate_ids,
            extracted_clues=dict(extracted_clues or {}),
            round_number=1,
            max_rounds=3,
            status=ResolutionSessionStatus.ACTIVE.value,
            partially_resolved_updates=list(partially_resolved_updates or []),
        )

        step_result = cls.generate_distinguishing_question(session, safe_candidates, resp_lang=resp_lang)
        return session, step_result

    @classmethod
    def _extract_activity_distinguishing_features(cls, act: Activity) -> Dict[str, Any]:
        """Extracts searchable and distinguishable attributes for an activity."""
        name_clean = act.name or ""
        # Location extraction from location_code or name
        loc = act.location_code
        if not loc:
            m = re.search(
                r"\b(Unit\s+\d+|Unit-\d+|Area\s+[A-Za-z0-9]+|Block\s+\d+|Pier\s+\d+|Foundation\s+(?:[A-Za-z]?\d+[A-Za-z0-9-]*|[A-Za-z]\b)|Level\s+\d+|F-\d+|यूनिट\s+\d+)\b",
                name_clean,
                re.IGNORECASE,
            )
            if m:
                loc = m.group(1).title()

        # Component / Distinctive Keyword
        norm_name = ActivityReferenceResolver.normalize_text(name_clean)
        tokens = [
            t for t in norm_name.split()
            if t not in {
                "mechanical", "civil", "electrical", "piping", "structural",
                "activity", "work", "package", "installation", "and", "the",
                "for", "to", "in", "at", "of", "phase", "pour", "testing",
            } and len(t) >= 3
        ]

        return {
            "id": act.id,
            "activity_code": act.activity_code,
            "name": name_clean,
            "discipline": act.discipline or "",
            "location": loc,
            "wbs_id": act.wbs_id,
            "key_tokens": tokens,
        }

    @classmethod
    def generate_distinguishing_question(
        cls,
        session: ActivityResolutionSession,
        candidate_activities: List[Activity],
        resp_lang: str = "en",
    ) -> ResolutionStepResult:
        """
        Deterministic Information-Gain Heuristic to select the most useful distinguishing question.
        Avoids generic 'can you provide more info' by surfacing real metadata partitions.
        """
        N = len(candidate_activities)
        if N == 0:
            session.status = ResolutionSessionStatus.FAILED.value
            return cls._make_failed_result(session, resp_lang, "no_candidates_remaining")

        if N == 1:
            # Uniquely resolved
            target = candidate_activities[0]
            session.resolved_activity_id = target.id
            session.status = ResolutionSessionStatus.RESOLVED.value
            return ResolutionStepResult(
                status=ResolutionSessionStatus.RESOLVED,
                resolved_activity=target,
                remaining_candidates=[target],
                audit_message=f"Candidate {target.activity_code} uniquely isolated.",
                round_number=session.round_number,
            )

        features = [cls._extract_activity_distinguishing_features(a) for a in candidate_activities]

        # -------------------------------------------------------------
        # Partition 1: Check distinct locations (e.g. Unit 4 vs Unit 5)
        # -------------------------------------------------------------
        loc_map: Dict[str, List[Activity]] = {}
        for f, a in zip(features, candidate_activities):
            loc = f.get("location")
            if loc:
                loc_map.setdefault(loc, []).append(a)

        # If locations divide the candidates into at least 2 non-empty subsets
        if len(loc_map) >= 2 and max(len(v) for v in loc_map.values()) < N:
            # Sort locations by candidate count
            sorted_locs = sorted(loc_map.keys(), key=lambda k: len(loc_map[k]), reverse=True)
            top_locs = sorted_locs[:3]
            loc_str = " or ".join(top_locs)

            if resp_lang == "hi":
                q_text = f"क्या यह कार्य {loc_str} में है?"
            elif resp_lang == "hinglish":
                q_text = f"Kya yeh work {loc_str} mein hai?"
            else:
                q_text = f"Is this work located in {loc_str}?"

            options = [
                {"label": loc, "value": loc} for loc in top_locs
            ]
            options.append({
                "label": "None of these" if resp_lang != "hi" else "इनमें से कोई नहीं",
                "value": "NONE_OF_THESE",
            })

            presented_opts = [
                {"index": idx + 1, "value": opt["value"], "label": opt["label"]}
                for idx, opt in enumerate(options)
            ]
            session.candidate_options_presented = presented_opts

            card = ActionCardDTO(
                type="CLARIFICATION_CHOICE",
                question=q_text,
                options=options,
            )
            session.current_question = {
                "question_text": q_text,
                "question_type": "location_partition",
                "locations": top_locs,
                "options": options,
            }
            session.asked_questions.append(session.current_question)

            return ResolutionStepResult(
                status=ResolutionSessionStatus.ACTIVE,
                question_text=q_text,
                action_card=card,
                remaining_candidates=candidate_activities,
                audit_message=f"Partitioned {N} candidates by location: {top_locs}",
                round_number=session.round_number,
            )

        # -------------------------------------------------------------
        # Partition 2: Direct Candidate Choices (Structured Akinator style)
        # Used when candidates <= 6 or as the primary specific differentiator
        # -------------------------------------------------------------
        display_candidates = candidate_activities[:6]

        disc_str = ""
        first_disc = candidate_activities[0].discipline or ""
        if all(a.discipline == first_disc for a in candidate_activities) and first_disc:
            disc_str = f" {first_disc.lower()}"

        if resp_lang == "hi":
            q_text = f"आप किस{disc_str} गतिविधि (activity) की बात कर रहे हैं?"
        elif resp_lang == "hinglish":
            q_text = f"Aap kaun-si{disc_str} activity update karna chahte hain?"
        else:
            q_text = f"Which{disc_str} activity do you mean?"

        options = []
        presented_opts = []
        for idx, act in enumerate(display_candidates):
            label = f"{act.activity_code} — {act.name}"
            options.append({
                "label": label,
                "value": act.activity_code,
                "activity_code": act.activity_code,
                "activity_name": act.name,
            })
            presented_opts.append({
                "index": idx + 1,
                "activity_id": act.id,
                "activity_code": act.activity_code,
                "activity_name": act.name,
                "label": label,
                "value": act.activity_code,
            })

        none_label = "इनमें से कोई नहीं" if resp_lang == "hi" else "None of these"
        options.append({
            "label": none_label,
            "value": "NONE_OF_THESE",
        })
        presented_opts.append({
            "index": len(options),
            "activity_id": None,
            "activity_code": "NONE_OF_THESE",
            "activity_name": none_label,
            "label": none_label,
            "value": "NONE_OF_THESE",
        })

        session.candidate_options_presented = presented_opts

        card = ActionCardDTO(
            type="CLARIFICATION_CHOICE",
            question=q_text,
            options=options,
        )
        session.current_question = {
            "question_text": q_text,
            "question_type": "direct_candidate_choice",
            "candidates": [a.activity_code for a in display_candidates],
            "options": options,
        }
        session.asked_questions.append(session.current_question)

        return ResolutionStepResult(
            status=ResolutionSessionStatus.ACTIVE,
            question_text=q_text,
            action_card=card,
            remaining_candidates=candidate_activities,
            audit_message=f"Presented direct candidate selection for {len(display_candidates)} activities.",
            round_number=session.round_number,
        )

    @classmethod
    def process_answer(
        cls,
        session: ActivityResolutionSession,
        user_answer: str,
        catalog: List[Activity],
        resp_lang: str = "en",
    ) -> ResolutionStepResult:
        """
        Interprets the user's clarification answer STRICTLY against the session's current candidate set.
        Never performs an unrestricted global search across the project.
        """
        clean_raw = (user_answer or "").strip()
        clean_ans = clean_raw.lower().rstrip(".,;:!?")
        norm_ans = ActivityReferenceResolver.normalize_text(clean_raw)
        ans_tokens = set(norm_ans.split())

        # Defense-in-depth: candidate activities strictly filtered by project_id
        current_candidates = [
            a for a in catalog
            if a.id in session.candidate_activity_ids and str(a.project_id) == str(session.project_id)
        ]

        if not current_candidates:
            session.status = ResolutionSessionStatus.FAILED.value
            return cls._make_failed_result(session, resp_lang, "no_candidates_in_project")

        # -------------------------------------------------------------
        # 1. Cancellation Check
        # -------------------------------------------------------------
        if clean_ans in cls.CANCELLATION_WORDS or any(clean_ans == w or clean_ans.startswith(w + " ") for w in cls.CANCELLATION_WORDS):
            session.status = ResolutionSessionStatus.CANCELLED.value
            session.history_log.append({
                "round": session.round_number,
                "answer": user_answer,
                "action": "CANCELLED_BY_USER",
            })
            if resp_lang == "hi":
                reply = "गतिविधि चयन रद्द कर दिया गया है। कोई अपडेट लागू नहीं किया गया।"
            elif resp_lang == "hinglish":
                reply = "Activity selection cancel kar diya gaya hai. Koi update apply nahi hua."
            else:
                reply = "Activity resolution was cancelled. No changes have been made."
            return ResolutionStepResult(
                status=ResolutionSessionStatus.CANCELLED,
                question_text=reply,
                audit_message="User cancelled activity resolution session.",
                round_number=session.round_number,
            )

        # -------------------------------------------------------------
        # 2. 'None of These' / 'Neither' Check
        # -------------------------------------------------------------
        is_none_of_these = (
            clean_ans in cls.NONE_OF_THESE_WORDS
            or any(w in clean_ans for w in ["none of these", "inme se koi nahi", "neither", "koi nahi"])
            or user_answer.strip().upper() == "NONE_OF_THESE"
        )
        if is_none_of_these:
            presented_ids = {
                opt["activity_id"] for opt in session.candidate_options_presented
                if opt.get("activity_id")
            }
            eliminated = [c for c in current_candidates if c.id in presented_ids]
            remaining = [c for c in current_candidates if c.id not in presented_ids]
            session.candidate_activity_ids = [c.id for c in remaining]
            session.history_log.append({
                "round": session.round_number,
                "answer": user_answer,
                "action": "ELIMINATED_PRESENTED",
                "eliminated": [c.activity_code for c in eliminated],
                "remaining_count": len(remaining),
            })

            if len(remaining) == 1:
                target = remaining[0]
                session.resolved_activity_id = target.id
                session.status = ResolutionSessionStatus.RESOLVED.value
                return ResolutionStepResult(
                    status=ResolutionSessionStatus.RESOLVED,
                    resolved_activity=target,
                    remaining_candidates=[target],
                    eliminated_candidates=eliminated,
                    audit_message=f"Resolved to only remaining candidate {target.activity_code} after None-of-These.",
                    round_number=session.round_number,
                )
            elif len(remaining) == 0 or session.round_number >= session.max_rounds:
                session.status = ResolutionSessionStatus.FAILED.value
                return cls._make_failed_result(session, resp_lang, "all_presented_eliminated")
            else:
                session.round_number += 1
                return cls.generate_distinguishing_question(session, remaining, resp_lang=resp_lang)

        # -------------------------------------------------------------
        # 3. Ordinal Match against immediate presented options
        # e.g. 'second one', '2nd', '2', 'pehla wala'
        # -------------------------------------------------------------
        matched_ordinal_idx: Optional[int] = None
        for ord_word, idx in cls.ORDINAL_MAP.items():
            if clean_ans == ord_word or clean_ans == f"the {ord_word}" or clean_ans == f"option {ord_word}":
                matched_ordinal_idx = idx
                break

        # Check raw integer input like '2' or '#2'
        if matched_ordinal_idx is None:
            m_digit = re.match(r"^#?(\d+)$", clean_ans)
            if m_digit:
                try:
                    val = int(m_digit.group(1))
                    # Only accept as ordinal if valid index in presented options (1 <= val <= count)
                    if 1 <= val <= len(session.candidate_options_presented):
                        matched_ordinal_idx = val
                except ValueError:
                    pass

        if matched_ordinal_idx is not None and session.candidate_options_presented:
            target_opt = None
            for opt in session.candidate_options_presented:
                if opt.get("index") == matched_ordinal_idx:
                    target_opt = opt
                    break

            if target_opt and target_opt.get("activity_id"):
                matched_act = next(
                    (c for c in current_candidates if c.id == target_opt["activity_id"]),
                    None,
                )
                if matched_act:
                    session.resolved_activity_id = matched_act.id
                    session.status = ResolutionSessionStatus.RESOLVED.value
                    session.history_log.append({
                        "round": session.round_number,
                        "answer": user_answer,
                        "action": "RESOLVED_ORDINAL",
                        "ordinal_index": matched_ordinal_idx,
                        "resolved_code": matched_act.activity_code,
                    })
                    return ResolutionStepResult(
                        status=ResolutionSessionStatus.RESOLVED,
                        resolved_activity=matched_act,
                        remaining_candidates=[matched_act],
                        audit_message=f"Resolved via ordinal #{matched_ordinal_idx} to {matched_act.activity_code}.",
                        round_number=session.round_number,
                    )
            elif target_opt and target_opt.get("value") == "NONE_OF_THESE":
                # User selected the ordinal corresponding to 'None of these'
                return cls.process_answer(session, "NONE_OF_THESE", catalog, resp_lang=resp_lang)

        # -------------------------------------------------------------
        # 4. Yes/No Identification Questions
        # e.g. "Is this the pump installation activity?" -> "yes" / "no"
        # -------------------------------------------------------------
        curr_q = session.current_question or {}
        if curr_q.get("question_type") == "yes_no" and curr_q.get("target_activity_id"):
            target_id = curr_q["target_activity_id"]
            matched_act = next((c for c in current_candidates if c.id == target_id), None)

            if clean_ans in cls.AFFIRMATIVE_WORDS or any(w in clean_ans for w in ["yes", "haan", "correct", "right", "हाँ"]):
                if matched_act:
                    session.resolved_activity_id = matched_act.id
                    session.status = ResolutionSessionStatus.RESOLVED.value
                    session.history_log.append({
                        "round": session.round_number,
                        "answer": user_answer,
                        "action": "AFFIRMED_CANDIDATE",
                        "resolved_code": matched_act.activity_code,
                    })
                    return ResolutionStepResult(
                        status=ResolutionSessionStatus.RESOLVED,
                        resolved_activity=matched_act,
                        remaining_candidates=[matched_act],
                        audit_message=f"Confirmed activity {matched_act.activity_code} via affirmative answer.",
                        round_number=session.round_number,
                    )

            if clean_ans in cls.NEGATIVE_WORDS or any(w in clean_ans for w in ["no", "nahi", "nahin", "not", "नहीं"]):
                remaining = [c for c in current_candidates if c.id != target_id]
                session.candidate_activity_ids = [c.id for c in remaining]
                session.history_log.append({
                    "round": session.round_number,
                    "answer": user_answer,
                    "action": "REJECTED_CANDIDATE",
                    "rejected_code": matched_act.activity_code if matched_act else target_id,
                })
                if len(remaining) == 1:
                    target = remaining[0]
                    session.resolved_activity_id = target.id
                    session.status = ResolutionSessionStatus.RESOLVED.value
                    return ResolutionStepResult(
                        status=ResolutionSessionStatus.RESOLVED,
                        resolved_activity=target,
                        remaining_candidates=[target],
                        audit_message=f"Resolved to single candidate {target.activity_code} after negative elimination.",
                        round_number=session.round_number,
                    )
                elif len(remaining) == 0 or session.round_number >= session.max_rounds:
                    session.status = ResolutionSessionStatus.FAILED.value
                    return cls._make_failed_result(session, resp_lang, "all_candidates_rejected")
                else:
                    session.round_number += 1
                    return cls.generate_distinguishing_question(session, remaining, resp_lang=resp_lang)

        # -------------------------------------------------------------
        # 5. Exact Activity Code Match against Current Candidates
        # e.g. 'MEC-1002', 'mec 1002', '1002'
        # -------------------------------------------------------------
        clean_compact = clean_ans.replace("-", "").replace("_", "").replace(" ", "").upper()
        for act in current_candidates:
            act_code_compact = act.activity_code.replace("-", "").replace("_", "").replace(" ", "").upper()
            if (
                act.activity_code.upper() == clean_raw.upper()
                or act_code_compact == clean_compact
                or act.activity_code.lower() in clean_ans
            ):
                session.resolved_activity_id = act.id
                session.status = ResolutionSessionStatus.RESOLVED.value
                session.history_log.append({
                    "round": session.round_number,
                    "answer": user_answer,
                    "action": "RESOLVED_EXACT_CODE",
                    "resolved_code": act.activity_code,
                })
                return ResolutionStepResult(
                    status=ResolutionSessionStatus.RESOLVED,
                    resolved_activity=act,
                    remaining_candidates=[act],
                    audit_message=f"Resolved via exact activity code {act.activity_code}.",
                    round_number=session.round_number,
                )

        # -------------------------------------------------------------
        # 6. Numeric suffix matching against current candidates
        # e.g. User says '1002' or '02'
        # -------------------------------------------------------------
        ref_nums = ActivityReferenceResolver.extract_numbers(norm_ans)
        if ref_nums:
            num_matched_candidates = []
            for num_val, num_str in ref_nums:
                for act in current_candidates:
                    digits = re.findall(r"\d+", act.activity_code)
                    if digits and any(int(d) == num_val or d.endswith(num_str) for d in digits):
                        if act not in num_matched_candidates:
                            num_matched_candidates.append(act)
                    # Check name numbers
                    name_digits = re.findall(r"\d+", act.name or "")
                    if name_digits and any(int(d) == num_val for d in name_digits):
                        if act not in num_matched_candidates:
                            num_matched_candidates.append(act)

            if len(num_matched_candidates) == 1:
                target = num_matched_candidates[0]
                session.resolved_activity_id = target.id
                session.status = ResolutionSessionStatus.RESOLVED.value
                session.history_log.append({
                    "round": session.round_number,
                    "answer": user_answer,
                    "action": "RESOLVED_NUMERIC_SUFFIX",
                    "resolved_code": target.activity_code,
                })
                return ResolutionStepResult(
                    status=ResolutionSessionStatus.RESOLVED,
                    resolved_activity=target,
                    remaining_candidates=[target],
                    audit_message=f"Resolved via numeric identifier {ref_nums} to {target.activity_code}.",
                    round_number=session.round_number,
                )
            elif len(num_matched_candidates) > 1:
                current_candidates = num_matched_candidates
                session.candidate_activity_ids = [c.id for c in current_candidates]

        # -------------------------------------------------------------
        # 7. Attribute / Location / Keyword Filtering
        # e.g. User says 'piping', 'the pump one', 'Unit 4'
        # -------------------------------------------------------------
        matching_acts: List[Tuple[Activity, float]] = []

        # Check location mentions (e.g. 'Unit 4')
        loc_matches = []
        for act in current_candidates:
            feats = cls._extract_activity_distinguishing_features(act)
            act_loc = feats.get("location")
            if act_loc and (act_loc.lower() in clean_ans or clean_ans in act_loc.lower()):
                loc_matches.append(act)
        if len(loc_matches) == 1:
            target = loc_matches[0]
            session.resolved_activity_id = target.id
            session.status = ResolutionSessionStatus.RESOLVED.value
            return ResolutionStepResult(
                status=ResolutionSessionStatus.RESOLVED,
                resolved_activity=target,
                remaining_candidates=[target],
                audit_message=f"Resolved uniquely via location '{clean_raw}' to {target.activity_code}.",
                round_number=session.round_number,
            )
        elif len(loc_matches) > 1:
            current_candidates = loc_matches
            session.candidate_activity_ids = [c.id for c in current_candidates]

        # Token and Substring matching across remaining candidates
        for act in current_candidates:
            act_norm = ActivityReferenceResolver.normalize_text(act.name or "")
            act_tokens = set(act_norm.split())

            score = 0.0
            # Full substring match
            if norm_ans in act_norm:
                score = 0.95
            else:
                meaningful_ref_tokens = {
                    t for t in ans_tokens
                    if t not in {"the", "a", "an", "is", "of", "in", "to", "for", "and", "one", "wale", "wala", "yes", "activity"}
                }
                if meaningful_ref_tokens:
                    inter = meaningful_ref_tokens.intersection(act_tokens)
                    if inter:
                        score = len(inter) / len(meaningful_ref_tokens)

            if score >= 0.5:
                matching_acts.append((act, score))

        if matching_acts:
            matching_acts.sort(key=lambda x: x[1], reverse=True)
            top_act, top_score = matching_acts[0]

            # Clear unique winner
            if len(matching_acts) == 1 or (len(matching_acts) > 1 and top_score >= matching_acts[1][1] + 0.3):
                session.resolved_activity_id = top_act.id
                session.status = ResolutionSessionStatus.RESOLVED.value
                session.history_log.append({
                    "round": session.round_number,
                    "answer": user_answer,
                    "action": "RESOLVED_KEYWORD",
                    "resolved_code": top_act.activity_code,
                    "score": top_score,
                })
                return ResolutionStepResult(
                    status=ResolutionSessionStatus.RESOLVED,
                    resolved_activity=top_act,
                    remaining_candidates=[top_act],
                    audit_message=f"Resolved via keyword match '{clean_raw}' to {top_act.activity_code} (score {top_score:.2f}).",
                    round_number=session.round_number,
                )
            else:
                # Multiple candidates matched the keyword: narrow candidate set and ask next question
                narrowed = [m[0] for m in matching_acts]
                session.candidate_activity_ids = [c.id for c in narrowed]
                current_candidates = narrowed

        # -------------------------------------------------------------
        # 8. Still Ambiguous or Unmatched: Advance Round or Fail
        # -------------------------------------------------------------
        session.history_log.append({
            "round": session.round_number,
            "answer": user_answer,
            "action": "UNRESOLVED_TURN",
            "candidates_count": len(current_candidates),
        })

        if session.round_number >= session.max_rounds:
            session.status = ResolutionSessionStatus.FAILED.value
            return cls._make_failed_result(session, resp_lang, "max_rounds_exceeded")

        session.round_number += 1
        return cls.generate_distinguishing_question(session, current_candidates, resp_lang=resp_lang)

    @classmethod
    def _make_failed_result(
        cls,
        session: ActivityResolutionSession,
        resp_lang: str,
        reason: str,
    ) -> ResolutionStepResult:
        session.status = ResolutionSessionStatus.FAILED.value
        example_code = "MEC-1002"
        if resp_lang == "hi":
            msg = (
                f"दी गई जानकारी से विशिष्ट गतिविधि की पहचान नहीं हो सकी। "
                f"कृपया शेड्यूल से सटीक गतिविधि कोड बताएं (जैसे {example_code}) या Activities Table में समीक्षा करें।"
            )
        elif resp_lang == "hinglish":
            msg = (
                f"Information ke basis par exact activity uniquely identify nahi ho saki. "
                f"Please schedule se exact activity code batayein (e.g. {example_code}) ya Activities Table check karein."
            )
        else:
            msg = (
                f"I couldn't uniquely identify the activity from the information provided. "
                f"Please provide the exact activity code (e.g., {example_code}) or select an activity from the Activities Table."
            )

        return ResolutionStepResult(
            status=ResolutionSessionStatus.FAILED,
            question_text=msg,
            action_card=None,
            remaining_candidates=[],
            audit_message=f"Activity resolution failed: {reason}",
            round_number=session.round_number,
        )
