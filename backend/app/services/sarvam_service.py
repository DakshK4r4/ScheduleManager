from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger("sarvam_service")

# Standard ScheduleManager construction and domain vocabulary
CORE_CONSTRUCTION_KEYTERMS: List[str] = [
    "Primavera",
    "P6",
    "CIV-1004",
    "CIV-1001",
    "CIV-1002",
    "F-204",
    "F-205",
    "WBS",
    "concrete",
    "concreting",
    "RCC",
    "PCC",
    "reinforcement",
    "rebar",
    "shuttering",
    "formwork",
    "foundation",
    "beam",
    "column",
    "slab",
    "piping",
    "cabling",
    "m3",
    "MT",
    "pour",
]

# Supported Indic language code mappings
SUPPORTED_INDIC_LANGUAGES: Dict[str, str] = {
    "en": "en-IN",
    "en-in": "en-IN",
    "hi": "hi-IN",
    "hi-in": "hi-IN",
    "hinglish": "hi-IN",
    "ta": "ta-IN",
    "ta-in": "ta-IN",
    "te": "te-IN",
    "te-in": "te-IN",
    "bn": "bn-IN",
    "bn-in": "bn-IN",
    "mr": "mr-IN",
    "mr-in": "mr-IN",
    "gu": "gu-IN",
    "gu-in": "gu-IN",
    "kn": "kn-IN",
    "kn-in": "kn-IN",
    "ml": "ml-IN",
    "ml-in": "ml-IN",
    "pa": "pa-IN",
    "pa-in": "pa-IN",
    "od": "od-IN",
    "od-in": "od-IN",
    "as": "as-IN",
    "as-in": "as-IN",
    "ur": "ur-IN",
    "ur-in": "ur-IN",
    "ne": "ne-IN",
    "ne-in": "ne-IN",
    "kok": "kok-IN",
    "kok-in": "kok-IN",
    "ks": "ks-IN",
    "ks-in": "ks-IN",
    "sd": "sd-IN",
    "sd-in": "sd-IN",
    "sa": "sa-IN",
    "sa-in": "sa-IN",
    "sat": "sat-IN",
    "sat-in": "sat-IN",
    "mni": "mni-IN",
    "mni-in": "mni-IN",
    "brx": "brx-IN",
    "brx-in": "brx-IN",
    "mai": "mai-IN",
    "mai-in": "mai-IN",
    "doi": "doi-IN",
    "doi-in": "doi-IN",
}

# Supported synchronous audio extensions and MIME types
SUPPORTED_AUDIO_EXTENSIONS: Set[str] = {
    ".wav",
    ".mp3",
    ".aac",
    ".aiff",
    ".ogg",
    ".opus",
    ".flac",
    ".mp4",
    ".m4a",
    ".webm",
    ".pcm",
    ".amr",
    ".wma",
}

MAX_SYNC_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB limit


class SarvamService:
    """
    Dedicated enterprise service encapsulating Sarvam AI capabilities:
    - Saaras v4 Multilingual Speech-to-Text with automatic language detection
    - Code-mixed audio handling (Hinglish, Tanglish, etc.)
    - Construction-specific dynamic keyterm priming (max 50 terms)
    - Token-shielded translation fallback for non-native response languages
    """

    _client = None

    @classmethod
    def get_client(cls):
        """Initializes and returns the official SarvamAI SDK client instance."""
        if cls._client is not None:
            return cls._client

        api_key = os.getenv("SARVAM_API_KEY")
        if not api_key:
            logger.warning("SARVAM_API_KEY environment variable is not configured.")
            return None

        try:
            from sarvamai import SarvamAI
            cls._client = SarvamAI(api_subscription_key=api_key)
            logger.info("SarvamAI client successfully initialized.")
            return cls._client
        except Exception as err:
            logger.error(f"Failed to initialize SarvamAI client: {err}")
            return None

    @classmethod
    def normalize_language_code(cls, lang: Optional[str]) -> str:
        """Converts short language tags (e.g. 'ta', 'hi') to Sarvam format (e.g. 'ta-IN', 'hi-IN')."""
        if not lang:
            return "en-IN"
        normalized = lang.lower().strip()
        return SUPPORTED_INDIC_LANGUAGES.get(normalized, lang)

    @classmethod
    def is_native_generation_supported(cls, lang: Optional[str]) -> bool:
        """
        Checks if native direct generation templates exist for the language.
        English, Hindi, and Hinglish have rich native direct templates.
        All other Indic languages use canonical answer generation + Sarvam translation fallback.
        """
        if not lang:
            return True
        norm = lang.lower().strip()
        return norm in {"en", "en-in", "hi", "hi-in", "hinglish"}

    @classmethod
    def validate_audio_file(cls, filename: str, file_bytes: bytes) -> Tuple[bool, Optional[str]]:
        """Validates file extension, length, and size constraints for synchronous STT."""
        if not file_bytes or len(file_bytes) == 0:
            return False, "Audio payload is empty."

        if len(file_bytes) > MAX_SYNC_AUDIO_BYTES:
            return False, f"Audio file size exceeds limit of {MAX_SYNC_AUDIO_BYTES // (1024*1024)}MB."

        ext = os.path.splitext(filename.lower())[1] if filename else ""
        if ext and ext not in SUPPORTED_AUDIO_EXTENSIONS:
            # Check for generic webm / wav blob names
            if not any(k in filename.lower() for k in ["webm", "wav", "audio", "blob", "recording"]):
                return False, f"Unsupported audio file format '{ext}'. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}"

        return True, None

    # Multilingual domain transliterations to bias both English and Indic STT recognition
    DISCIPLINE_KEYTERMS_MAP: Dict[str, List[str]] = {
        "elec": ["Electrical", "इलेक्ट्रिकल", "इलेक्ट्रिक", "केबल"],
        "civi": ["Civil", "सिविल", "कंक्रीट", "फाउंडेशन"],
        "stru": ["Structural", "स्ट्रक्चरल", "लोहा", "बीम"],
        "pipi": ["Piping", "पाइपिंग", "पाइप"],
        "mech": ["Mechanical", "मैकेनिकल"],
        "inst": ["Instrumentation", "इंस्ट्रूमेंटेशन"],
        "insu": ["Insulation", "इंसुलेशन"],
        "pain": ["Painting", "पेंटिंग"],
    }

    COMMON_ACTION_KEYTERMS: List[str] = [
        "अपडेट", "एक्टिविटीज", "एक्टिविटी", "काम", "कार्य", "खत्म", "पूरा"
    ]

    @classmethod
    def build_project_keyterms(cls, db: Session, project_id: str) -> List[str]:
        """
        Dynamically extracts up to 50 construction keyterms from the active project
        to prime Sarvam Saaras v4 STT model across both English and Indic languages.
        """
        keyterms: List[str] = []
        seen: Set[str] = set()

        def add_term(term: Optional[str]):
            if not term:
                return
            cleaned = term.strip()
            if 2 <= len(cleaned) <= 30 and cleaned.lower() not in seen and len(keyterms) < 50:
                seen.add(cleaned.lower())
                keyterms.append(cleaned)

        from app.domain.models import Activity, Project, WBSNode

        # 1. Project code and name
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            add_term(project.project_code)
            if project.name and project.name != project.project_code:
                for w in project.name.split():
                    if len(w) > 3 and not w.isdigit():
                        add_term(w)

        # 2. Add WBS nodes
        wbs_nodes = db.query(WBSNode).filter(WBSNode.project_id == project_id).limit(8).all()
        for wbs in wbs_nodes:
            add_term(wbs.code)
            if wbs.name:
                for w in wbs.name.split():
                    if len(w) > 3:
                        add_term(w)

        # 3. Add all project disciplines in English and Devanagari
        all_acts = db.query(Activity).filter(Activity.project_id == project_id).all()
        found_disc_keys: Set[str] = set()
        for act in all_acts:
            name_low = (act.name or "").lower()
            disc_low = (act.discipline or "").lower()
            code_prefix = act.activity_code.split("-")[0].lower() if "-" in act.activity_code else ""
            for d_key in cls.DISCIPLINE_KEYTERMS_MAP:
                if d_key in name_low or d_key in disc_low or d_key in code_prefix:
                    found_disc_keys.add(d_key)

        for d_key in sorted(found_disc_keys):
            for t in cls.DISCIPLINE_KEYTERMS_MAP[d_key]:
                add_term(t)

        # 4. Add common action keyterms (Devanagari)
        for t in cls.COMMON_ACTION_KEYTERMS:
            add_term(t)

        # 5. Add representative activity codes (distributed across disciplines)
        acts_by_prefix: Dict[str, List[Activity]] = {}
        for act in all_acts:
            pref = act.activity_code.split("-")[0] if "-" in act.activity_code else "GEN"
            acts_by_prefix.setdefault(pref, []).append(act)

        for pref, pref_acts in sorted(acts_by_prefix.items()):
            for act in pref_acts[:2]:
                add_term(act.activity_code)
                if act.location_code:
                    add_term(act.location_code)

        # 6. Fill with domain terminology up to 50 keyterms
        for term in CORE_CONSTRUCTION_KEYTERMS:
            add_term(term)

        return keyterms[:50]

    @classmethod
    def transcribe_audio(
        cls,
        file_bytes: bytes,
        filename: str = "audio.webm",
        language_code: str = "unknown",
        mode: str = "transcribe",
        keyterms: Optional[List[str]] = None,
        prompt_keyterms: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Transcribes audio using Sarvam Saaras v4 model.
        Returns a dict containing:
        - transcript: str
        - detected_language_code: str (e.g. 'hi-IN', 'ta-IN', 'en-IN')
        - language_probability: Optional[float]
        """
        is_valid, err_msg = cls.validate_audio_file(filename, file_bytes)
        if not is_valid:
            raise ValueError(err_msg or "Invalid audio file.")

        client = cls.get_client()
        if client is None:
            # Graceful offline / test fallback if SARVAM_API_KEY is missing
            logger.warning("Sarvam client not available; returning fallback transcript.")
            return {
                "transcript": "Audio received (Sarvam offline)",
                "detected_language_code": "en-IN",
                "language_probability": 1.0,
            }

        # Format keyterms: max 50 terms
        cleaned_keyterms = (keyterms or prompt_keyterms or [])[:50]

        # Prepare file payload
        file_stream = io.BytesIO(file_bytes)
        file_stream.name = filename

        try:
            logger.info(
                f"Calling Sarvam Saaras v4 STT: filename={filename}, mode={mode}, "
                f"language_code={language_code}, keyterms_count={len(cleaned_keyterms)}"
            )
            resp = client.speech_to_text.transcribe(
                file=file_stream,
                model="saaras:v4",
                mode=mode if mode in ["transcribe", "codemix", "verbatim", "translate"] else "transcribe",
                language_code=language_code if language_code else "unknown",
                keyterms=cleaned_keyterms if cleaned_keyterms else None,
            )

            transcript = (getattr(resp, "transcript", "") or "").strip()
            detected_lang = getattr(resp, "language_code", None) or "en-IN"
            prob = getattr(resp, "language_probability", None)

            # Analyze code-mixing and language details
            from app.services.agent_parser import ConversationalParser
            lang_analysis = ConversationalParser.analyze_language_and_codemixing(
                text=transcript,
                audio_detected_lang=detected_lang,
            )

            is_code_mixed = lang_analysis.get("is_code_mixed", False)
            detected_languages = lang_analysis.get("detected_languages", [lang_analysis.get("short_code", "en")])
            short_code = lang_analysis.get("short_code", "en")
            primary_lang = lang_analysis.get("primary_language", detected_lang)
            style = lang_analysis.get("language_style", "standard")

            low_confidence = False
            if prob is not None and prob < 0.20:
                low_confidence = True

            logger.info(
                f"Sarvam STT success: detected={detected_lang}, short={short_code}, "
                f"is_code_mixed={is_code_mixed}, detected_langs={detected_languages}, prob={prob}"
            )

            return {
                "transcription": transcript,
                "transcript": transcript,
                "detected_language": short_code,
                "detected_language_code": primary_lang,
                "is_code_mixed": is_code_mixed,
                "detected_languages": detected_languages,
                "language_style": style,
                "language_probability": prob,
                "confidence": prob if prob is not None else 1.0,
                "low_confidence": low_confidence,
            }
        except Exception as e:
            logger.error(f"Error during Sarvam STT: {e}")
            raise RuntimeError(f"Speech transcription failed: {str(e)}")

    @classmethod
    def shield_technical_tokens(cls, text: str) -> Tuple[str, Dict[str, str]]:
        """
        Protects technical identifiers, codes, percentages, numbers, and units
        from being phonetically or erroneously altered by machine translation.
        Encloses tokens in `[[TOKEN]]` which Sarvam translation models preserve intact.
        """
        token_map: Dict[str, str] = {}
        counter = 0

        # Pattern matches:
        # 1. Activity codes like CIV-1001, STR-2004, WBS-001
        # 2. Location tags like F-204, B-102
        # 3. Numbers with units like 35 m3, 100 MT, 50.0%
        # 4. Explicit percentage values like 14%, 80.0%
        pattern = re.compile(
            r"\b[A-Z]{2,5}-\d{3,5}\b|"        # CIV-1001
            r"\b[A-Z]-\d{2,4}\b|"             # F-204
            r"\bWBS-[A-Za-z0-9_\-]+\b|"      # WBS-CIV
            r"\b\d+(?:\.\d+)?\s*(?:m3|m³|MT|tons|sqm|meter|cubic meter)\b|" # 35 m3
            r"\b\d+(?:\.\d+)?%\b",            # 80%
            re.IGNORECASE,
        )

        def repl(match):
            nonlocal counter
            val = match.group(0)
            token_key = f"[[{val}]]"
            return token_key

        shielded = pattern.sub(repl, text)
        return shielded, token_map

    @classmethod
    def unshield_technical_tokens(cls, text: str) -> str:
        """Removes the protective brackets `[[` and `]]` from the translated string and cleans stray tokens."""
        unshielded = re.sub(r"\[\[\s*(.*?)\s*\]\]", r"\1", text)
        # Clean up stray artifact tokens that may be inserted by translation models
        unshielded = re.sub(r"\.text\s*:\s*", " ", unshielded)
        unshielded = re.sub(r"\.உரை\s*:\s*", " ", unshielded)
        return unshielded

    @classmethod
    def translate_text(
        cls,
        text: str,
        source_language_code: str = "en-IN",
        target_language_code: str = "hi-IN",
        numerals_format: str = "international",
    ) -> str:
        """
        Translates text using Sarvam's official translation service (mayura:v1).
        Preserves technical codes and numbers through token shielding.
        """
        if not text or not text.strip():
            return text

        src = cls.normalize_language_code(source_language_code)
        tgt = cls.normalize_language_code(target_language_code)

        if src == tgt:
            return text

        client = cls.get_client()
        if client is None:
            logger.warning("Sarvam client not configured; returning un-translated text.")
            return text

        shielded_text, _ = cls.shield_technical_tokens(text)

        try:
            logger.info(f"Calling Sarvam Translation: {src} -> {tgt} (length={len(text)})")
            resp = client.text.translate(
                input=shielded_text,
                source_language_code=src if src in SUPPORTED_INDIC_LANGUAGES.values() else "auto",
                target_language_code=tgt,
                numerals_format="international",
                model="mayura:v1",
            )
            translated = getattr(resp, "translated_text", "") or shielded_text
            unshielded = cls.unshield_technical_tokens(translated)
            return unshielded
        except Exception as err:
            logger.error(f"Sarvam translation failed: {err}")
            # Fallback to original text if translation fails, rather than crashing
            return text

    @classmethod
    def clean_text_for_speech(cls, text: str) -> str:
        """
        Cleans markdown formatting, links, bullets, and excessive symbols
        from assistant response text to ensure clean and natural pronunciation.
        """
        if not text:
            return ""
        # Strip code blocks
        cleaned = re.sub(r"```[\s\S]*?```", "", text)
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        # Strip markdown links [text](url) -> text
        cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
        # Strip bold / italics **text** -> text, *text* -> text
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
        cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)
        # Strip markdown headers # Header -> Header
        cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
        # Replace markdown bullet points with a comma for natural pausing
        cleaned = re.sub(r"^\s*[-*•]\s+", ", ", cleaned, flags=re.MULTILINE)
        # Remove bracketed system badges like [Critical Path] -> Critical Path
        cleaned = re.sub(r"\[([^\]]+)\]", r"\1", cleaned)
        # Collapse multiple spaces and newlines
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @classmethod
    def text_to_speech(
        cls,
        text: str,
        language_code: Optional[str] = "en-IN",
        speaker: Optional[str] = None,
        model: str = "bulbul:v3",
    ) -> Dict[str, Any]:
        """
        Converts text to speech using Sarvam Bulbul V3 TTS model.
        Returns a dict containing:
        - audio_base64: str (base64-encoded audio)
        - content_type: str ("audio/wav")
        - language_code: str (e.g. 'en-IN', 'hi-IN')
        - speaker: str
        """
        if not text or not text.strip():
            raise ValueError("Text payload for text-to-speech cannot be empty.")

        speech_text = cls.clean_text_for_speech(text)
        if not speech_text:
            raise ValueError("Text content after markdown cleaning is empty.")

        # Respect model limit (2500 chars for bulbul:v3)
        if len(speech_text) > 2400:
            boundary = max(
                speech_text[:2400].rfind(". "),
                speech_text[:2400].rfind("। "),
                speech_text[:2400].rfind("? "),
                speech_text[:2400].rfind("! "),
            )
            if boundary > 500:
                speech_text = speech_text[:boundary + 1]
            else:
                speech_text = speech_text[:2400].rsplit(" ", 1)[0] + "..."

        # Normalize and validate language code
        norm_lang = cls.normalize_language_code(language_code)

        # If language was unspecified or generic, analyze text script
        if not norm_lang or norm_lang in ("auto", "unknown", "en-IN"):
            from app.services.agent_parser import ConversationalParser
            detected_analysis = ConversationalParser.analyze_language_and_codemixing(speech_text)
            detected_primary = detected_analysis.get("primary_language")
            if detected_primary and detected_primary != "en-IN":
                norm_lang = detected_primary

        supported_tts_langs = {
            "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN",
            "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"
        }
        if norm_lang not in supported_tts_langs:
            short = norm_lang.split("-")[0].lower() if "-" in norm_lang else norm_lang.lower()
            candidate = f"{short}-IN"
            norm_lang = candidate if candidate in supported_tts_langs else "en-IN"

        # Resolve speaker based on model version
        chosen_speaker = speaker.lower() if speaker else ""
        if model == "bulbul:v3":
            v3_speakers = {
                "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan",
                "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
                "manan", "sumit", "roopa", "kabir", "aayan", "ashutosh", "advait", "anand",
                "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
                "mohit", "kavitha", "rehan", "soham", "rupali"
            }
            if not chosen_speaker or chosen_speaker not in v3_speakers:
                chosen_speaker = "shubh"
        else:
            v2_speakers = {"anushka", "manisha", "vidya", "arya", "abhilash", "karun", "hitesh"}
            if not chosen_speaker or chosen_speaker not in v2_speakers:
                chosen_speaker = "anushka"

        client = cls.get_client()
        if client is None:
            logger.warning("SARVAM_API_KEY is not configured on the server.")
            raise ValueError("SARVAM_API_KEY is not configured on the server.")

        logger.info(
            f"Calling Sarvam Bulbul TTS: model={model}, lang={norm_lang}, speaker={chosen_speaker}, chars={len(speech_text)}"
        )
        try:
            resp = client.text_to_speech.convert(
                text=speech_text,
                language_code=norm_lang,
                speaker=chosen_speaker,
                model=model,
            )
            audios = getattr(resp, "audios", None) or []
            if not audios:
                raise RuntimeError("Sarvam TTS service returned an empty audio payload.")

            return {
                "audio_base64": audios[0],
                "content_type": "audio/wav",
                "language_code": norm_lang,
                "speaker": chosen_speaker,
            }
        except Exception as err:
            logger.error(f"Error during Sarvam text-to-speech: {err}")
            raise RuntimeError(f"Text-to-speech synthesis failed: {str(err)}")
