from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.models import Activity

logger = logging.getLogger("activity_reference_resolver")


@dataclass
class ResolutionResult:
    resolved_activity: Optional[Activity] = None
    confidence: float = 0.0
    method: str = "none"  # exact_code, exact_name, normalized_name_numeric, suffix_numeric, controlled_fuzzy, ambiguous, no_match, none
    candidates: List[Activity] = field(default_factory=list)
    ambiguity_reason: Optional[str] = None
    normalized_reference: str = ""
    match_score: float = 0.0
    resolution_status: str = "NO_MATCH"  # RESOLVED, AMBIGUOUS, NO_MATCH

    def __post_init__(self):
        if self.resolved_activity is not None:
            self.resolution_status = "RESOLVED"
        elif self.method == "ambiguous":
            self.resolution_status = "AMBIGUOUS"
        else:
            self.resolution_status = "NO_MATCH"


class ActivityReferenceResolver:
    """
    Project-aware Activity Reference Resolver.
    Separates natural language activity references from authoritative project activity lookup.
    Never selects an entire discipline merely because the discipline matches.
    Resolution priority:
      1. Exact activity_code (e.g. MEC-1002, MEC 1002, MEC1002)
      2. Exact activity name
      3. Exact normalized activity reference against normalized activity name
      4. Activity code suffix / numeric identifier (e.g. 1002 -> MEC-1002)
      5. Normalized name + numeric identifier (e.g. 'mechanical activity 02' or 'mechanical 2' -> MEC-1002)
      6. Controlled typo-tolerant fuzzy matching (e.g. 'acitivity 2', 'mecahnical 02')
      7. Ambiguous -> clarification (never auto-bulk)
      8. No Match -> explicit NO_MATCH without auto-selection
    """

    RESOLUTION_THRESHOLD: float = 0.65
    SUGGESTION_THRESHOLD: float = 0.35
    MARGIN_DELTA_THRESHOLD: float = 0.25

    GENERIC_WORDS: Set[str] = {
        "the", "a", "an", "is", "was", "are", "were", "of", "in", "to", "for", "and", "at", "on", "by", "with",
        "activity", "activities", "task", "tasks", "work", "works",
        "update", "updates", "job", "jobs", "item", "items", "act",
        "i", "we", "you", "they", "he", "she", "it", "my", "our",
        "completed", "complete", "completing", "finished", "finish", "done", "started", "start", "progress",
        "today", "yesterday", "now", "kal", "aaj",
        "kary", "kaam", "karo", "karna", "gaya", "hai", "hain", "tha", "thi",
    }

    TYPO_CORRECTIONS = {
        "acitivity": "activity",
        "activty": "activity",
        "acitvity": "activity",
        "activitiy": "activity",
        "actvity": "activity",
        "mecahnical": "mechanical",
        "mechanic": "mechanical",
        "mech": "mechanical",
        "structrual": "structural",
        "structual": "structural",
        "electical": "electrical",
        "electric": "electrical",
        "pipng": "piping",
        "pipping": "piping",
        "instrumnt": "instrumentation",
        "insulaton": "insulation",
    }

    DISCIPLINE_CANONICAL = {
        "mec": "Mechanical",
        "mech": "Mechanical",
        "mechanical": "Mechanical",
        "civ": "Civil",
        "civil": "Civil",
        "ele": "Electrical",
        "electrical": "Electrical",
        "pip": "Piping",
        "pipe": "Piping",
        "piping": "Piping",
        "str": "Structural",
        "structure": "Structural",
        "structural": "Structural",
        "ins": "Instrumentation",
        "instrumentation": "Instrumentation",
        "insu": "Insulation",
        "insulation": "Insulation",
        "pai": "Painting",
        "painting": "Painting",
    }

    DEVANAGARI_TRANSLATIONS = {
        "मैकेनिकल": "mechanical",
        "मैकेनिक": "mechanical",
        "सिविल": "civil",
        "इलेक्ट्रिकल": "electrical",
        "इलेक्ट्रिक": "electrical",
        "विद्युत": "electrical",
        "बिजली": "electrical",
        "पाइपिंग": "piping",
        "पाइप": "piping",
        "स्ट्रक्चरल": "structural",
        "स्ट्रक्चर": "structural",
        "ढांचा": "structural",
        "वेल्डिंग": "welding",
        "वेल्ड": "welding",
        "एक्टिविटी": "activity",
        "गतिविधि": "activity",
        "टास्क": "task",
        "कार्य": "work",
        "काम": "work",
        "इंसुलेशन": "insulation",
        "पेंटिंग": "painting",
        "रंगाई": "painting",
        "रंग": "painting",
        "इंस्ट्रूमेंटेशन": "instrumentation",
        "उपकरण": "instrumentation",
        "फाउंडेशन": "foundation",
        "नींव": "foundation",
        "कंक्रीट": "concrete",
        "खुदाई": "excavation",
        "खनन": "excavation",
        "इंस्टॉलेशन": "installation",
        "स्थापना": "installation",
        "कमीशनिंग": "commissioning",
        "परीक्षण": "testing",
        "टेस्टिंग": "testing",
    }

    @classmethod
    def normalize_text(cls, text: str) -> str:
        if not text:
            return ""
        clean = text.lower().strip()
        # Indic digits normalization
        from app.services.agent_parser import ConversationalParser
        clean = ConversationalParser.normalize_indic_digits(clean)
        # Translate Devanagari construction terms to canonical Latin terms
        for dev, eng in cls.DEVANAGARI_TRANSLATIONS.items():
            if dev in clean:
                clean = clean.replace(dev, f" {eng} ")
        # Apply typo corrections
        words = re.findall(r"[A-Za-z0-9]+", clean)
        corrected = [cls.TYPO_CORRECTIONS.get(w, w) for w in words]
        return " ".join(corrected)

    @classmethod
    def extract_numbers(cls, text: str) -> List[Tuple[int, str]]:
        """
        Extracts numbers from text with both integer value and raw string (preserving zero-padding).
        e.g. '02' -> (2, '02'), '1' -> (1, '1')
        """
        matches = re.finditer(r"\b(\d+)\b", text)
        results = []
        for m in matches:
            raw = m.group(1)
            try:
                results.append((int(raw), raw))
            except ValueError:
                pass
        return results

    @classmethod
    def resolve(
        cls,
        project_id: str,
        catalog: List[Activity],
        raw_reference: str,
        explicit_discipline: Optional[str] = None,
    ) -> ResolutionResult:
        """
        Authoritatively resolves a single natural language activity reference against
        the project's activity catalog.
        """
        if not catalog or not raw_reference or not raw_reference.strip():
            return ResolutionResult(
                resolved_activity=None,
                confidence=0.0,
                method="none",
                candidates=[],
                ambiguity_reason="empty_input_or_catalog",
                normalized_reference="",
            )

        norm_ref = cls.normalize_text(raw_reference)
        ref_numbers = cls.extract_numbers(norm_ref)

        # Filter catalog strictly to project_id (Defense-in-depth safety)
        project_acts = [a for a in catalog if str(a.project_id) == str(project_id)]
        if not project_acts:
            return ResolutionResult(
                resolved_activity=None,
                confidence=0.0,
                method="none",
                candidates=[],
                ambiguity_reason="no_activities_in_project",
                normalized_reference=norm_ref,
            )

        # -------------------------------------------------------------
        # STAGE 1: Exact Activity Code Match
        # -------------------------------------------------------------
        # Test full string and tokens against code variants (e.g. MEC-1002, MEC1002, MEC 1002)
        norm_ref_compact = norm_ref.replace("-", "").replace("_", "").replace(" ", "").upper()
        for act in project_acts:
            code_raw = act.activity_code.upper().strip()
            code_compact = code_raw.replace("-", "").replace("_", "").replace(" ", "")
            code_spaced = code_raw.replace("-", " ").replace("_", " ").lower()
            code_hyphen = code_raw.lower()

            if (
                code_raw == raw_reference.strip().upper()
                or code_compact == norm_ref_compact
                or code_hyphen in norm_ref
                or code_spaced in norm_ref
                or code_compact in norm_ref_compact
            ):
                # Strong exact code signal
                logger.info(f"Resolved reference '{raw_reference}' via exact_code -> {act.activity_code}")
                return ResolutionResult(
                    resolved_activity=act,
                    confidence=1.0,
                    method="exact_code",
                    candidates=[act],
                    normalized_reference=norm_ref,
                    match_score=1.0,
                )

        # -------------------------------------------------------------
        # STAGE 2: Exact Activity Name Match
        # -------------------------------------------------------------
        for act in project_acts:
            if act.name and act.name.strip().lower() == raw_reference.strip().lower():
                logger.info(f"Resolved reference '{raw_reference}' via exact_name -> {act.activity_code}")
                return ResolutionResult(
                    resolved_activity=act,
                    confidence=1.0,
                    method="exact_name",
                    candidates=[act],
                    normalized_reference=norm_ref,
                    match_score=1.0,
                )

        # -------------------------------------------------------------
        # STAGE 3: Exact Normalized Name Match
        # -------------------------------------------------------------
        for act in project_acts:
            act_norm_name = cls.normalize_text(act.name or "")
            if act_norm_name and act_norm_name == norm_ref:
                logger.info(f"Resolved reference '{raw_reference}' via normalized_exact_name -> {act.activity_code}")
                return ResolutionResult(
                    resolved_activity=act,
                    confidence=0.99,
                    method="normalized_exact_name",
                    candidates=[act],
                    normalized_reference=norm_ref,
                    match_score=0.99,
                )

        # -------------------------------------------------------------
        # STAGE 4: Suffix / Numeric Code Match (e.g. '1002' -> MEC-1002)
        # -------------------------------------------------------------
        if ref_numbers:
            matching_by_code_num = []
            for num_val, num_str in ref_numbers:
                if len(num_str) >= 3:  # Only for full code digits like 1001, 1002, 204
                    for act in project_acts:
                        act_code_digits = re.findall(r"\d+", act.activity_code)
                        if act_code_digits and any(int(d) == num_val for d in act_code_digits):
                            if act not in matching_by_code_num:
                                matching_by_code_num.append(act)

            if len(matching_by_code_num) == 1:
                act = matching_by_code_num[0]
                logger.info(f"Resolved reference '{raw_reference}' via suffix_numeric -> {act.activity_code}")
                return ResolutionResult(
                    resolved_activity=act,
                    confidence=0.95,
                    method="suffix_numeric",
                    candidates=[act],
                    normalized_reference=norm_ref,
                    match_score=0.95,
                )

        # -------------------------------------------------------------
        # STAGE 5: Normalized Name + Numeric Identifier Matching
        # Handles:
        # "mechanical activity 1", "mechanical activity 01" -> MEC-1001 (Mechanical Activity 01)
        # "mechanical activity 2", "mechanical activity 02" -> MEC-1002 (Mechanical Activity 02)
        # "piping 03" -> PIP-1003 (Piping 03)
        # -------------------------------------------------------------
        # Identify targeted discipline in reference or context
        ref_words = set(norm_ref.split())
        ref_discipline = None
        for disc_alias, canon in cls.DISCIPLINE_CANONICAL.items():
            if disc_alias in ref_words:
                ref_discipline = canon
                break
        if not ref_discipline and explicit_discipline:
            ref_discipline = cls.DISCIPLINE_CANONICAL.get(explicit_discipline.lower(), explicit_discipline)

        # Pre-filter by discipline if specified
        discipline_filtered_acts = project_acts
        if ref_discipline:
            discipline_filtered_acts = [
                a for a in project_acts
                if (a.discipline and ref_discipline.lower() in a.discipline.lower())
                or (ref_discipline.lower() in (a.name or "").lower())
                or (ref_discipline == "Mechanical" and a.activity_code.startswith("MEC-"))
                or (ref_discipline == "Civil" and a.activity_code.startswith("CIV-"))
                or (ref_discipline == "Electrical" and a.activity_code.startswith("ELE-"))
                or (ref_discipline == "Piping" and a.activity_code.startswith("PIP-"))
                or (ref_discipline == "Structural" and a.activity_code.startswith("STR-"))
                or (ref_discipline == "Instrumentation" and a.activity_code.startswith("INS-"))
            ]

        if ref_numbers:
            # Look for activity whose name or code ends with the numeric identifier
            # e.g. ref_number = 2 (from '2' or '02')
            # matches 'Mechanical Activity 02' where trailing number is 2!
            name_numeric_matches: List[Tuple[Activity, float]] = []

            for act in discipline_filtered_acts:
                act_name_clean = cls.normalize_text(act.name or "")
                act_name_numbers = cls.extract_numbers(act_name_clean)
                act_code_numbers = cls.extract_numbers(act.activity_code)

                matched_score = 0.0
                for num_val, num_str in ref_numbers:
                    # Check if act.name contains this exact number
                    for act_num_val, act_num_str in act_name_numbers:
                        if act_num_val == num_val:
                            # Direct number equality!
                            # Higher score if padding matches ('02' == '02'), but allow '2' == '02'
                            matched_score = max(matched_score, 0.95 if act_num_str == num_str else 0.90)

                    # Also check if act.activity_code has corresponding suffix digit
                    # e.g. MEC-1002 has 1002, which ends with 02 or 2
                    for act_c_val, act_c_str in act_code_numbers:
                        if act_c_val == num_val or act_c_str.endswith(num_str) or (num_val < 100 and act_c_val % 1000 == num_val):
                            matched_score = max(matched_score, 0.88)

                if matched_score > 0.0:
                    name_numeric_matches.append((act, matched_score))

            if len(name_numeric_matches) == 1:
                act, score = name_numeric_matches[0]
                logger.info(f"Resolved reference '{raw_reference}' via normalized_name_numeric -> {act.activity_code}")
                return ResolutionResult(
                    resolved_activity=act,
                    confidence=score,
                    method="normalized_name_numeric",
                    candidates=[act],
                    normalized_reference=norm_ref,
                    match_score=score,
                )
            elif len(name_numeric_matches) > 1:
                # If multiple match, check if one matches the discipline much closer
                best_matches = [m for m in name_numeric_matches if m[1] == max(x[1] for x in name_numeric_matches)]
                if len(best_matches) == 1:
                    act, score = best_matches[0]
                    return ResolutionResult(
                        resolved_activity=act,
                        confidence=score,
                        method="normalized_name_numeric",
                        candidates=[act],
                        normalized_reference=norm_ref,
                        match_score=score,
                    )
                else:
                    cands = [m[0] for m in name_numeric_matches]
                    return ResolutionResult(
                        resolved_activity=None,
                        confidence=0.5,
                        method="ambiguous",
                        candidates=cands,
                        ambiguity_reason="multiple_name_numeric_matches",
                        normalized_reference=norm_ref,
                    )

        # -------------------------------------------------------------
        # STAGE 6: Controlled Typo-Tolerant Token Matching
        # -------------------------------------------------------------
        scored_candidates: List[Tuple[Activity, float]] = []
        suggestion_candidates: List[Tuple[Activity, float]] = []
        ref_tokens = {w for w in norm_ref.split() if w not in cls.GENERIC_WORDS}
        if not ref_tokens:
            ref_tokens = {w for w in norm_ref.split() if w not in {"the", "a", "an", "is", "of", "in", "to", "for", "and"}}

        eval_pool = discipline_filtered_acts if discipline_filtered_acts else project_acts

        for act in eval_pool:
            act_name_norm = cls.normalize_text(act.name or "")
            act_tokens = set(act_name_norm.split())
            if not act_tokens:
                continue

            intersection = ref_tokens.intersection(act_tokens)
            coverage = len(intersection) / len(ref_tokens) if ref_tokens else 0.0
            jaccard = len(intersection) / len(ref_tokens.union(act_tokens)) if ref_tokens.union(act_tokens) else 0.0
            score = 0.7 * coverage + 0.3 * jaccard

            if score >= cls.RESOLUTION_THRESHOLD:
                scored_candidates.append((act, score))
            elif score >= cls.SUGGESTION_THRESHOLD:
                suggestion_candidates.append((act, score))

        if scored_candidates:
            scored_candidates.sort(key=lambda x: x[1], reverse=True)
            top_act, top_score = scored_candidates[0]
            if len(scored_candidates) == 1 or (len(scored_candidates) > 1 and top_score >= scored_candidates[1][1] + cls.MARGIN_DELTA_THRESHOLD):
                logger.info(f"Resolved reference '{raw_reference}' via controlled_fuzzy -> {top_act.activity_code}")
                return ResolutionResult(
                    resolved_activity=top_act,
                    confidence=round(top_score, 2),
                    method="controlled_fuzzy",
                    candidates=[top_act],
                    normalized_reference=norm_ref,
                    match_score=round(top_score, 2),
                )
            else:
                return ResolutionResult(
                    resolved_activity=None,
                    confidence=round(top_score, 2),
                    method="ambiguous",
                    candidates=[c[0] for c in scored_candidates[:10]],
                    ambiguity_reason="multiple_fuzzy_matches",
                    normalized_reference=norm_ref,
                )

        # -------------------------------------------------------------
        # STAGE 7: Ambiguous or No Match (Section 32)
        # -------------------------------------------------------------
        non_discipline_tokens = {
            w for w in ref_tokens
            if w not in cls.DISCIPLINE_CANONICAL and (not ref_discipline or w != ref_discipline.lower())
        }

        if discipline_filtered_acts and len(discipline_filtered_acts) > 1 and ref_discipline and not non_discipline_tokens:
            # The user only specified a broad discipline without any distinguishing identifier (e.g. "mechanical activity", "civil")
            return ResolutionResult(
                resolved_activity=None,
                confidence=0.3,
                method="ambiguous",
                candidates=discipline_filtered_acts[:10],
                ambiguity_reason="broad_discipline_needs_clarification",
                normalized_reference=norm_ref,
            )

        # No scheduled activity in the project schedule reached the resolution safety threshold.
        # This is strictly NO_MATCH. Plausible candidates above SUGGESTION_THRESHOLD are suggestions only.
        suggestion_candidates.sort(key=lambda x: x[1], reverse=True)
        top_sugg_score = round(suggestion_candidates[0][1], 2) if suggestion_candidates else 0.0
        return ResolutionResult(
            resolved_activity=None,
            confidence=0.0,
            method="no_match",
            candidates=[c[0] for c in suggestion_candidates[:5]],
            ambiguity_reason="no_matching_scheduled_activity",
            normalized_reference=norm_ref,
            match_score=top_sugg_score,
        )

