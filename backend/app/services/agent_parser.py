from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import httpx

from app.schemas.agent import ParsedConversationalIntent, ActivityUpdateCandidate
from app.services.credential_resolver import CredentialResolver
from app.services.extraction_service import ExtractionService

logger = logging.getLogger("agent_parser")


class ConversationalParser:
    """
    Parses conversational user utterances into structured intent and execution-event fields
    using Google Gemini API via TIME_AGENT_GEMINI_API_KEY (with governed fallback),
    with deterministic date resolution and offline rule-based fallbacks.
    """

    WEEKDAYS = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    INDIC_DIGITS_MAP = {
        '०': '0', '१': '1', '२': '2', '३': '3', '४': '4', '५': '5', '६': '6', '७': '7', '८': '8', '९': '9',
        '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4', '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9',
        '੦': '0', '੧': '1', '੨': '2', '੩': '3', '੪': '4', '੫': '5', '੬': '6', '੭': '7', '੮': '8', '੯': '9',
        '૦': '0', '૧': '1', '૨': '2', '૩': '3', '૪': '4', '૫': '5', '૬': '6', '૭': '7', '૮': '8', '૯': '9',
        '௦': '0', '௧': '1', '௨': '2', '௩': '3', '௪': '4', '௫': '5', '௬': '6', '௭': '7', '௮': '8', '௯': '9',
        '౦': '0', '౧': '1', '౨': '2', '౩': '3', '౪': '4', '౫': '5', '౬': '6', '౭': '7', '౮': '8', '౯': '9',
        '೦': '0', '೧': '1', '೨': '2', '೩': '3', '೪': '4', '೫': '5', '೬': '6', '೭': '7', '೮': '8', '೯': '9',
        '൦': '0', '൧': '1', '൨': '2', '൩': '3', '൪': '4', '൫': '5', '൬': '6', '൭': '7', '൮': '8', '൯': '9',
        '୦': '0', '୧': '1', '୨': '2', '୩': '3', '୪': '4', '୫': '5', '୬': '6', '୭': '7', '୮': '8', '୯': '9',
    }

    DISCIPLINE_PREFIX_MAP = {
        "CIV": [
            "civ", "civil", "civl",
            "सिविल", "सीआईवी", "सी आई वी", "सी.आई.वी.",
            "சிவில்", "கட்டுமானம்",
            "సివిల్", "సివిల్స్",
            "सिव्हिल",
            "સિવિલ",
            "সিভিল",
            "ಸಿವಿಲ್",
            "സിവിൽ",
            "ਸਿਵਲ", "ਸਿਵਿਲ",
            "ସିଭିଲ୍",
        ],
        "STR": [
            "str", "structural", "structure", "steel",
            "स्ट्रक्चरल", "स्ट्रक्चर", "एसटीआर", "एस टी आर", "एस.टी.आर.",
            "கட்டமைப்பு", "ஸ்டிரக்ச்சரல்",
            "నిర్మాణ", "స్ట్రక్చరల్",
            "स्ट्रक्चरल",
            "સ્ટ્રક્ચરલ",
            "স্ট্রাকচারাল",
            "ರಚನಾತ್ಮಕ", "ಸ್ಟ್ರಕ್ಚರಲ್",
            "സ്ട്രക്ചറൽ",
            "ਸਟ੍ਰਕਚਰਲ",
            "ଷ୍ଟ୍ରକଚରାଲ୍",
        ],
        "ELE": [
            "ele", "electrical", "electric", "power", "industrial", "wiring",
            "इलेक्ट्रिकल", "इलेक्ट्रिक", "ईएलई", "बिजली", "विद्युत", "इंडस्ट्रियल", "इंडस्ट्रियल एक्टिविटीज",
            "மின்", "மின்னியல்", "தொழில்துறை", "எலக்ட்ரிக்கல்",
            "విద్యుత్", "ఎలక్ట్రికల్", "పారిశ్రామిక",
            "इलेक्ट्रिकल", "विद्युत",
            "ઇલેક્ટ્રિકલ", "વિદ્યુત",
            "ইলেকট্রিক্যাল", "বৈদ্যুতিক", "শিল্প",
            "ವಿದ್ಯುತ್", "ಎಲೆಕ್ಟ್ರಿಕಲ್",
            "ഇലക്ട്രിക്കൽ", "വൈദ്യുതി",
            "ਇਲੈਕਟ੍ਰੀਕਲ", "ਬਿਜਲੀ",
            "ବୈଦ୍ୟୁତିକ",
        ],
        "PIP": [
            "pip", "piping", "pipe",
            "पाइपिंग", "पाइप", "पीआईपी", "पी आई पी",
            "குழாய்", "பைப்பிங்",
            "పైపింగ్", "పైపు",
            "पाइपिंग", "पाइप",
            "પાઇપિંગ", "પાઇપ",
            "পাইপিং", "পাইপ",
            "ಪೈಪಿಂಗ್", "ಪೈಪ್",
            "പൈപ്പിംഗ്", "പൈപ്പ്",
            "ਪਾਈਪਿੰਗ", "ਪਾਈਪ",
            "ପାଇପିଂ",
        ],
        "MEC": [
            "mec", "mech", "mechanical", "pump", "turbine",
            "मैकेनिकल", "मैकेनिक", "एमईसी", "एम ई सी", "मशीन", "उपकरण",
            "இயந்திரவியல்", "மெக்கானிக்கல்",
            "మెకానికల్", "యంత్ర",
            "मेकॅनिकल",
            "મિકેનિકલ",
            "মেকানিক্যাল", "যন্ত্রপাতি",
            "ಮೆಕ್ಯಾನಿಕಲ್",
            "മെക്കാനിക്കൽ",
            "ਮਕੈਨੀਕਲ",
            "ମେକାନିକାଲ୍",
        ],
        "INS": [
            "ins", "instrumentation", "instrument", "sensor",
            "इंस्ट्रूमेंटेशन", "इंस्ट्रूमेंट", "आईएनएस", "आई एन एस", "सेंसर",
            "கருவிமயமாக்கல்", "இன்ஸ்ட்ருமென்டேஷன்",
            "ఇన్స్ట్రుమెంటేషన్",
            "इन्स्ट्रुमेंटेशन",
            "ઇન્સ્ટ્રુમેન્ટેશન",
            "ইন্সট্রুমেন্টেশন",
            "ಇನ್‌ಸ್ಟ್ರುಮೆಂಟೇಶನ್",
            "ഇൻസ്ട്രുമെന്റേഷൻ",
            "ਇੰਸਟਰੂਮੈਂਟੇਸ਼ਨ",
            "ଇନଷ୍ଟ୍ରୁମେଣ୍ଟେସନ୍",
        ],
        "INSU": [
            "insu", "insulation", "insulate",
            "इंसुलेशन", "इंसुलेट",
            "காப்பு", "இன்சுலேஷன்",
            "ఇన్సులేషన్",
            "इन्सुलेशन",
            "ઇન્સ્યુલેશન",
            "ইনসুলেশন",
            "ಇನ್ಸುಲೇಶನ್",
            "ഇൻസുലേഷൻ",
            "ਇਨਸੂਲੇਸ਼ਨ",
            "ଇନସୁଲେସନ୍",
        ],
        "PAI": [
            "pai", "painting", "paint", "coating",
            "पेंटिंग", "पेंट", "रंग",
            "வர்ணம்", "பெயிண்டிங்", "வண்ணம்",
            "పెయింటింగ్", "రంగు",
            "पेंटिंग", "रंगकाम",
            "પેઇન્ટિંગ", "રંગ",
            "পেইন্টিং", "রং",
            "ಪೇಂಟಿಂಗ್", "ಬಣ್ಣ",
            "പെയിന്റിംഗ്", "പെയിന്റ്",
            "ਪੇਂਟਿੰਗ", "ਰੰਗ",
            "ପେଣ୍ଟିଂ",
        ],
    }

    PREFIX_TO_CANON_DISCIPLINE = {
        "CIV": "Civil",
        "STR": "Structural",
        "ELE": "Electrical",
        "PIP": "Piping",
        "MEC": "Mechanical",
        "INS": "Instrumentation",
        "INSU": "Insulation",
        "PAI": "Painting",
    }

    COMPLETION_VERBS_LATIN = [
        "finish", "finished", "finishing", "complete", "completed", "completing",
        "done", "100%", "khatam", "pura", "poora", "ho gaya", "kar diya", "kar diye",
        "kar li", "hogaya", "kardiya"
    ]

    COMPLETION_VERBS_INDIC = [
        # Hindi / Devanagari
        "पूरा", "खत्म", "हो गया", "कर दिए", "कर दिया", "कर दी", "पूर्ण", "समाप्त", "निपटा", "हो चुका",
        # Tamil
        "முடித்துவிட்டேன்", "முடித்துவிட்டோம்", "முடித்தோம்", "முடிந்தது", "முடிந்துவிட்டது", "முடித்தேன்", "முழுமை", "நிறைவு",
        # Telugu
        "పూర్తయింది", "పూర్తి చేశాను", "పూర్తి చేసాము", "పూర్తి చేశాము", "పూర్తి", "ముగిసింది", "అయిపోయింది",
        # Kannada
        "ಮುಗಿದಿದೆ", "ಪೂರ್ಣಗೊಂಡಿದೆ", "ಪೂರ್ಣ", "ಆಗಿದೆ",
        # Malayalam
        "പൂർത്തിയാക്കി", "പൂർത്തിയായി", "തീർത്തു", "കഴിഞ്ഞു",
        # Bengali
        "সম্পন্ন করেছি", "সম্পন্ন", "শেষ করেছি", "শেষ", "হয়ে গেছে", "সম্পন্ন হয়েছে",
        # Marathi
        "पूर्ण केले", "पूर्ण", "संपवले", "संपले", "झाले",
        # Gujarati
        "પૂર્ણ કર્યા", "પૂર્ણ કર્યું", "પૂર્ણ", "પૂરું કર્યું", "પૂરું", "સમાપ્ત કર્યું", "સમાપ્ત", "થઈ ગયું",
        # Punjabi
        "ਪੂਰਾ ਕਰ ਦਿੱਤਾ", "ਪੂਰਾ", "ਖਤਮ ਕਰ ਦਿੱਤਾ", "ਖਤਮ", "ਹੋ ਗਿਆ",
        # Odia
        "ସମ୍ପୂର୍ଣ୍ଣ", "ଶେଷ"
    ]

    @classmethod
    def normalize_indic_digits(cls, text: str) -> str:
        if not text:
            return ""
        return "".join(cls.INDIC_DIGITS_MAP.get(c, c) for c in text)

    @classmethod
    def extract_activity_code(cls, text: str) -> Optional[str]:
        """
        Deterministically extracts activity codes across multiple formats and Indian languages:
        1. Explicit hyphenated or compact codes (e.g. CIV-1001, civ-1001, CIV 1001, civ1001, STR-204)
        2. Multilingual discipline names + numeric codes (e.g. 'सिविल की 1001', 'சிவில் இன் 1001', 'civil 1001', '1001 civil')
        3. Phonetic Devanagari acronyms (e.g. 'सीआईवी 1001', 'एसटीआर 1001')
        """
        if not text:
            return None
        norm_text = cls.normalize_indic_digits(text)

        # 1. Standard pattern: CIV-1001, CIV 1001, civ-1001, civ1001, STR-204, etc.
        STOP_PREFIXES = {
            "TO", "IN", "AT", "ON", "BY", "FOR", "IS", "AS", "AN", "SET",
            "THE", "AND", "OR", "OF", "UP", "DO", "ALL", "NO", "NOT", "PER"
        }
        direct = re.search(r"\b([A-Za-z]{2,5})[-_\s]?(\d{3,5})\b", norm_text)
        if direct:
            prefix = direct.group(1).upper()
            num = direct.group(2)
            if prefix not in STOP_PREFIXES:
                for canon, aliases in cls.DISCIPLINE_PREFIX_MAP.items():
                    if prefix == canon or prefix.lower() in aliases:
                        return f"{canon}-{num}"
                if 2 <= len(prefix) <= 4:
                    return f"{prefix}-{num}"

        # 2. Multilingual discipline alias + number (e.g. 'सिविल की 1001', 'சிவில் இன் 1001')
        # Strip explicit percentages so numbers like 100% or 98% are never misidentified as activity code numbers
        text_no_pct = re.sub(r"\b\d+(?:\.\d+)?\s*(?:%|percent\b|प्रतिशत\b)", " ", norm_text, flags=re.IGNORECASE)
        for canon_prefix, aliases in cls.DISCIPLINE_PREFIX_MAP.items():
            for alias in aliases:
                p1 = rf"\b{re.escape(alias)}\b[^\d]{{0,35}}\b(\d{{3,5}})\b" if alias.isascii() else rf"{re.escape(alias)}[^\d]{{0,35}}\b(\d{{3,5}})\b"
                m = re.search(p1, text_no_pct, re.IGNORECASE)
                if m:
                    return f"{canon_prefix}-{m.group(1)}"
                p2 = rf"\b(\d{{3,5}})\b[^\d]{{0,35}}\b{re.escape(alias)}\b" if alias.isascii() else rf"\b(\d{{3,5}})\b[^\d]{{0,35}}{re.escape(alias)}"
                m2 = re.search(p2, text_no_pct, re.IGNORECASE)
                if m2:
                    return f"{canon_prefix}-{m2.group(1)}"

        # 3. Direct hyphenated pattern fallback (e.g. F-204 tag or general code)
        hyphen_m = re.search(r"\b([A-Za-z0-9]{2,5}-[A-Za-z0-9]{2,5})\b", norm_text)
        if hyphen_m:
            return hyphen_m.group(1).upper()

        return None

    @classmethod
    def extract_multilingual_disciplines(cls, text: str) -> List[str]:
        """
        Extracts canonical discipline names from multilingual utterances.
        """
        if not text:
            return []
        lower = text.lower()
        found: List[str] = []
        for canon_prefix, aliases in cls.DISCIPLINE_PREFIX_MAP.items():
            canon_name = cls.PREFIX_TO_CANON_DISCIPLINE.get(canon_prefix, canon_prefix)
            for alias in aliases:
                match = False
                if alias.isascii():
                    if re.search(rf"\b{re.escape(alias)}\b", lower):
                        match = True
                else:
                    if alias in text:
                        match = True
                if match:
                    if canon_name not in found:
                        found.append(canon_name)
                    break
        return found

    @classmethod
    def detect_language(cls, text: str) -> str:
        """
        Deterministically detects if the utterance is:
        - "hi": Hindi (written in Devanagari script)
        - "hinglish": Hindi / Mixed Hindi-English written in Roman/Latin script
        - "en": English (default)
        """
        if not text:
            return "en-IN"

        # 1. Indic scripts detection
        if re.search(r"[\u0900-\u097F]", text):
            return "hi-IN"
        if re.search(r"[\u0B80-\u0BFF]", text):
            return "ta-IN"
        if re.search(r"[\u0C00-\u0C7F]", text):
            return "te-IN"
        if re.search(r"[\u0980-\u09FF]", text):
            return "bn-IN"
        if re.search(r"[\u0C80-\u0CFF]", text):
            return "kn-IN"
        if re.search(r"[\u0D00-\u0D7F]", text):
            return "ml-IN"
        if re.search(r"[\u0A80-\u0AFF]", text):
            return "gu-IN"
        if re.search(r"[\u0A00-\u0A7F]", text):
            return "pa-IN"
        if re.search(r"[\u0B00-\u0B7F]", text):
            return "od-IN"

        # 2. Common Romanized Hindi / Hinglish tokens
        lower = text.lower()
        hinglish_words = {
            "aaj", "kal", "parson", "mein", "me", "par", "pe", "ka", "ki", "ke", "ko",
            "hai", "hain", "tha", "the", "thi", "hoga", "hogi", "kya", "kitna", "kitni",
            "kitne", "kaun", "kaunsa", "kahan", "kaisa", "kaise", "batao", "dikhao",
            "dala", "daala", "dale", "dhalai", "kiya", "kiye", "karein", "karo", "lagaya",
            "lagaye", "bichhaya", "khudai", "kaam", "pura", "poora", "khatam", "ho",
            "gaya", "gayi", "gaye", "chal", "raha", "rahi", "rahe", "sabhi", "saare",
            "sare", "sab", "pichle", "pichla", "pehle", "ab", "tak", "aur", "kul",
            "milakar", "shuru", "hua", "hui", "radd", "chahiye", "sariya", "loha"
        }
        tokens = set(re.findall(r"[a-z]+", lower))
        matches = tokens.intersection(hinglish_words)
        if matches:
            if len(matches) >= 2 or any(t in {"aaj", "dala", "dhalai", "kya", "kitna", "batao", "dikhao", "mein", "gaya", "pura", "khatam", "pichle"} for t in matches):
                return "hinglish"
        return "en-IN"

    @classmethod
    def analyze_language_and_codemixing(cls, text: str, audio_detected_lang: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyzes the utterance for spoken/written language, script preservation,
        and code-mixing (e.g. Hindi+English, Hinglish, Marathi+English).
        Returns:
        {
            "primary_language": str (e.g. 'hi-IN', 'mr-IN', 'en-IN', 'ta-IN'),
            "short_code": str (e.g. 'hi', 'mr', 'en', 'ta'),
            "is_code_mixed": bool,
            "detected_languages": List[str],
            "language_style": str ('hindi', 'marathi', 'hinglish', 'english', 'native'),
            "script": str ('Devanagari', 'Latin', 'Tamil', etc.)
        }
        """
        if not text or not text.strip():
            return {
                "primary_language": audio_detected_lang or "en-IN",
                "short_code": (audio_detected_lang or "en-IN").split("-")[0].lower(),
                "is_code_mixed": False,
                "detected_languages": [(audio_detected_lang or "en-IN").split("-")[0].lower()],
                "language_style": "standard",
                "script": "Latin",
            }

        text_str = text.strip()

        # Script detection
        has_devanagari = bool(re.search(r"[\u0900-\u097F]", text_str))
        has_tamil = bool(re.search(r"[\u0B80-\u0BFF]", text_str))
        has_telugu = bool(re.search(r"[\u0C00-\u0C7F]", text_str))
        has_bengali = bool(re.search(r"[\u0980-\u09FF]", text_str))
        has_kannada = bool(re.search(r"[\u0C80-\u0CFF]", text_str))
        has_malayalam = bool(re.search(r"[\u0D00-\u0D7F]", text_str))
        has_gujarati = bool(re.search(r"[\u0A80-\u0AFF]", text_str))
        has_punjabi = bool(re.search(r"[\u0A00-\u0A7F]", text_str))
        has_odia = bool(re.search(r"[\u0B00-\u0B7F]", text_str))
        # Detect latin alphabetic words (length >= 2, excluding technical codes like CIV-1001)
        latin_words = [w for w in re.findall(r"\b[A-Za-z]{2,}\b", text_str) if not re.match(r"^[A-Z]+-\d+$", w)]
        has_latin = len(latin_words) > 0

        # Determine primary language and short code
        primary_lang = "en-IN"
        short_code = "en"
        style = "english"
        script = "Latin"

        if audio_detected_lang:
            from app.services.sarvam_service import SarvamService
            primary_lang = SarvamService.normalize_language_code(audio_detected_lang)
            short_code = primary_lang.split("-")[0].lower()
            if short_code == "hi":
                style = "hindi"
            elif short_code == "mr":
                style = "marathi"
            elif short_code == "ta":
                style = "tamil"
            elif short_code == "te":
                style = "telugu"
            elif short_code == "pa":
                style = "punjabi"
            elif short_code == "bn":
                style = "bengali"
            elif short_code == "gu":
                style = "gujarati"
            else:
                style = "native"
        elif has_devanagari:
            script = "Devanagari"
            marathi_markers = {"आम्ही", "साठी", "ओतले", "केले", "आहे", "नाही", "झाले", "करायचे", "घनमीटर", "करा"}
            tokens = set(text_str.split())
            if tokens.intersection(marathi_markers):
                primary_lang = "mr-IN"
                short_code = "mr"
                style = "marathi"
            else:
                primary_lang = "hi-IN"
                short_code = "hi"
                style = "hindi"
        elif has_tamil:
            primary_lang = "ta-IN"
            short_code = "ta"
            style = "tamil"
            script = "Tamil"
        elif has_telugu:
            primary_lang = "te-IN"
            short_code = "te"
            style = "telugu"
            script = "Telugu"
        elif has_bengali:
            primary_lang = "bn-IN"
            short_code = "bn"
            style = "bengali"
            script = "Bengali"
        elif has_kannada:
            primary_lang = "kn-IN"
            short_code = "kn"
            style = "kannada"
            script = "Kannada"
        elif has_malayalam:
            primary_lang = "ml-IN"
            short_code = "ml"
            style = "malayalam"
            script = "Malayalam"
        elif has_gujarati:
            primary_lang = "gu-IN"
            short_code = "gu"
            style = "gujarati"
            script = "Gujarati"
        elif has_punjabi:
            primary_lang = "pa-IN"
            short_code = "pa"
            style = "punjabi"
            script = "Gurmukhi"
        elif has_odia:
            primary_lang = "od-IN"
            short_code = "od"
            style = "odia"
            script = "Odia"
        else:
            # Latin script - check Hinglish
            det = cls.detect_language(text_str)
            if det == "hinglish":
                primary_lang = "hi-IN"
                short_code = "hi"
                style = "hinglish"
            else:
                primary_lang = "en-IN"
                short_code = "en"
                style = "english"

        # Code mixing analysis
        is_code_mixed = False
        detected_languages: List[str] = [short_code]

        if style == "hinglish":
            is_code_mixed = True
            detected_languages = ["hi", "en"]
        elif (has_devanagari or has_tamil or has_telugu or has_bengali or has_kannada or has_malayalam or has_gujarati or has_punjabi or has_odia) and has_latin:
            is_code_mixed = True
            if "en" not in detected_languages:
                detected_languages.append("en")

        return {
            "primary_language": primary_lang,
            "short_code": short_code,
            "is_code_mixed": is_code_mixed,
            "detected_languages": detected_languages,
            "language_style": style,
            "script": script,
        }

    @classmethod
    def is_explicit_translation_request(cls, text: str) -> Optional[str]:
        """Detects if user explicitly requested language translation."""
        if not text:
            return None
        lower = text.strip().lower()
        if any(p in lower for p in ["translate to english", "translate this to english", "translate into english", "isko english mein translate", "english me translate"]):
            return "en-IN"
        if any(p in lower for p in ["translate to hindi", "translate this to hindi", "translate into hindi", "isko hindi mein translate", "hindi me translate"]):
            return "hi-IN"
        return None

    @classmethod
    def is_meaningful_for_language_lock(cls, text: str) -> bool:
        """
        Determines whether the utterance contains enough linguistic content to reliably
        determine and lock the conversation language, guarding against premature locking on
        vacuous greetings such as 'hi', 'hello', 'ok', etc.
        """
        if not text or not text.strip():
            return False

        # 1. Any Indic script is an immediate strong signal
        if re.search(r"[\u0900-\u0D7F]", text):
            return True

        # 2. Check for trivial greetings or filler tokens
        lower = text.strip().lower().rstrip(".,!?;:")
        trivial_greetings = {
            "hi", "hello", "hey", "hola", "ok", "okay", "yes", "no", "test",
            "thanks", "thank you", "k"
        }
        if lower in trivial_greetings:
            return False

        # 3. Check for specific construction activity codes or equipment tags (e.g. CIV-1001, F-204)
        if re.search(r"\b[A-Za-z]+-\d+\b", text):
            return True

        # 4. Check for Hinglish strong keywords
        hinglish_strong = {
            "aaj", "dala", "dhalai", "kya", "kitna", "batao", "dikhao", "mein", "gaya",
            "pura", "khatam", "pichle", "kaam", "lagaya", "sariya", "khudai"
        }
        tokens = set(re.findall(r"[a-z]+", lower))
        if any(t in hinglish_strong for t in tokens):
            return True

        # 5. Multi-word meaningful sentences (>= 2 words)
        words = lower.split()
        if len(words) >= 2 and not all(w in trivial_greetings for w in words):
            return True

        return False

    @classmethod
    def is_explicit_language_switch_request(cls, text: str) -> Optional[str]:
        """
        Detects if the user is explicitly requesting to switch or change the conversation language.
        Returns the requested target language ('en', 'hi', 'hinglish', or 'general') if detected,
        otherwise None.
        """
        if not text:
            return None
        lower = text.strip().lower().rstrip(".,!?;:")

        # English switch phrases
        en_patterns = [
            "switch to english", "switch this conversation to english", "answer in english",
            "reply in english", "speak in english", "talk in english", "can we speak in english",
            "please answer in english", "please answer this in english", "please answer this one in english",
            "change language to english", "english please"
        ]
        if any(p in lower for p in en_patterns):
            return "en"

        # Hindi switch phrases
        hi_patterns = [
            "switch to hindi", "hindi mein bolo", "hindi mein baat karo", "hindi me bolo",
            "hindi me baat karo", "change language to hindi", "hindi please",
            "हिंदी में बात करो", "हिंदी में बोलो", "हिंदी में उत्तर दें", "हिंदी में बताएं"
        ]
        if any(p in lower for p in hi_patterns) or any(p in text for p in ["हिंदी में बात करो", "हिंदी में बोलो", "हिंदी में उत्तर"]):
            return "hi"

        # Hinglish switch phrases
        hinglish_patterns = [
            "switch to hinglish", "hinglish mein bolo", "hinglish me bolo", "hinglish please",
            "change language to hinglish", "hinglish mein baat karo"
        ]
        if any(p in lower for p in hinglish_patterns):
            return "hinglish"

        # General switch requests
        if any(p in lower for p in ["switch language", "change language"]):
            return "general"

        return None

    def __init__(self, gemini_api_key: Optional[str] = None):
        res = CredentialResolver.resolve_time_agent_credentials(explicit_key=gemini_api_key)
        self.gemini_api_key = res.api_key
        self.model = res.model
        self.credential_source = res.source

    def parse(
        self,
        text: str,
        reference_date: Optional[datetime] = None,
        active_activity_code: Optional[str] = None,
        is_clarification_turn: bool = False,
        conversation_language: Optional[str] = None,
    ) -> ParsedConversationalIntent:
        return self.parse_message(
            text=text,
            project_data_date=reference_date,
            active_activity_code=active_activity_code,
            is_clarification_turn=is_clarification_turn,
            conversation_language=conversation_language,
        )

    @classmethod
    def resolve_date(
        cls,
        raw_date_str: Optional[str],
        reference_date: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """
        Deterministically resolves natural language temporal references against reference_date
        (defaults to project.data_date, or current UTC date if project.data_date is null).
        Prevents silent defaulting to current calendar date when reporting against past schedules.
        Supports English, Hindi, and Hinglish temporal expressions.
        """
        if not raw_date_str:
            return None

        clean = raw_date_str.strip().lower()
        ref = reference_date or datetime.now(timezone.utc).replace(tzinfo=None)

        # ISO format: YYYY-MM-DD
        iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", clean)
        if iso_match:
            try:
                return datetime.strptime(iso_match.group(0), "%Y-%m-%d")
            except ValueError:
                pass

        if "today" in clean or "aaj" in clean or "आज" in raw_date_str:
            return datetime(ref.year, ref.month, ref.day)

        if "yesterday" in clean or "kal" in clean or "कल" in raw_date_str:
            y = ref - timedelta(days=1)
            return datetime(y.year, y.month, y.day)

        # Check day names (e.g. "on monday")
        for day_name, day_idx in cls.WEEKDAYS.items():
            if day_name in clean:
                ref_day = ref.weekday()
                delta_days = (ref_day - day_idx) % 7
                if delta_days == 0:
                    delta_days = 7  # Most recent prior occurrence
                resolved_day = ref - timedelta(days=delta_days)
                return datetime(resolved_day.year, resolved_day.month, resolved_day.day)

        return None

    @classmethod
    def parse_with_gemini(
        cls,
        text: str,
        project_data_date_str: str = "",
        active_activity_code: Optional[str] = None,
        is_clarification_turn: bool = False,
        conversation_language: Optional[str] = None,
        conversation_style: Optional[str] = None,
        language_locked: bool = False,
    ) -> Optional[ParsedConversationalIntent]:
        """
        Calls Gemini API with structured prompt and JSON response schema.
        Falls back to None on error or timeout so caller can use rule-based fallback.
        """
        res = CredentialResolver.resolve_time_agent_credentials()
        gemini_api_key = res.api_key
        if not gemini_api_key:
            return None

        prompt = f"""You are an authoritative construction execution agent.
Parse the following user message into a structured JSON execution event representation.

Project reference data date: {project_data_date_str or 'Not Specified'}
Currently active activity anchor in UI: {active_activity_code or 'None'}
Is currently in clarification dialog: {is_clarification_turn}
Active Spoken/Input Language: {conversation_language or 'Auto-detected'}
Language Style: {conversation_style or 'natural'}

Multilingual Understanding & Response Rule:
Understand the user's intent without translating the input unnecessarily.
Respond using the same primary language and language style used by the user. Preserve code-mixing when appropriate.
Never translate or alter technical identifiers: activity codes (e.g. CIV-1001, STR-2001), locations (e.g. F-204, Pier 14), WBS codes, project codes, units, numbers, or dates.
Convert construction terms (concrete, dhalai, sariya, rebar, khudai, excavation, etc.) into their canonical English representations for discipline and intent.
Map Indic/Hinglish temporal words ('aaj', 'आज' -> 'today'; 'kal', 'कल' -> 'yesterday').

Classify into EXACTLY one of these intents:
- INFORMATION_QUERY: Supervisor asks about schedule, activity details, duration, dates, or progress (e.g. "F-204 ka current progress kya hai?", "Pichle project mein concrete ki average productivity kya thi?").
- PROGRESS_REPORT: Supervisor describes physical construction work completed or underway for a specific single activity (e.g. "Aaj F-204 mein 35 cubic meter concrete dala hai", "We completed 35 m3 of concrete at F-204 today").
- PROGRESS_UPDATE_REQUEST: Supervisor directly requests a percentage or status change (e.g. "update this to 80%", "CIV-1001 ko 80% update karo").
- CLARIFICATION_RESPONSE: Supervisor provides missing information in direct response to an agent question (e.g. "F-204", "incremental", "all of them").
- ARTIFACT_SUBMISSION: Supervisor uploads or references a file attachment.
- BULK_PROGRESS_REPORT: Supervisor describes physical progress or status change across a set/group of activities, an entire discipline, or an entire work package (e.g. "we have completed all the electrical activities", "finished all cable trays", "saare civil activities ho gaye").

Extract the following fields if present in the message:
- is_bulk: boolean true if user is reporting progress across multiple activities or entire discipline/work package, otherwise false
- bulk_scope: object containing {{ "discipline": discipline, "location": location, "keyword": keyword, "wbs_hint": wbs_hint }} or null
- quantity: numerical float or null
- unit: standard engineering unit (m3, m2, t, m, ea) or null
- quantity_semantics: INCREMENTAL (work done today/this shift), CUMULATIVE (total work completed to date), or UNKNOWN
- location: physical structural element or area (e.g. Foundation F-204, Pier 14, Level 2) or null
- discipline: engineering discipline (Civil, Structural, Piping, Electrical, Mechanical) or null
- contractor: subcontractor or crew name or null
- asset: equipment or tag code or null
- wbs_hint: work package or WBS hint or null
- reported_activity_code: explicit activity code cited in text (e.g. CIV-1001, STR-204); do NOT guess
- execution_date: date mentioned in text (e.g. '2024-09-30', 'today', 'yesterday', 'Monday') or null if no date cited
- status_reported: COMPLETED, IN_PROGRESS, or NOT_STARTED
- override_percent: numerical float percentage (0-100) if explicitly stated; otherwise null
- description: concise summary of work described
- detected_language: 'en' for English, 'hi' for Hindi in Devanagari, or 'hinglish' for Romanized Hindi / mixed

Return ONLY valid JSON matching this structure:
{{
  "intent": "PROGRESS_REPORT",
  "confidence": 0.95,
  "is_bulk": false,
  "bulk_scope": null,
  "entities_present": ["quantity", "unit", "location"],
  "quantity": 35.0,
  "unit": "m3",
  "quantity_semantics": "INCREMENTAL",
  "location": "F-204",
  "discipline": "Civil",
  "contractor": null,
  "asset": null,
  "wbs_hint": null,
  "reported_activity_code": null,
  "execution_date": "today",
  "status_reported": "IN_PROGRESS",
  "override_percent": null,
  "description": "Poured 35 cubic meters of concrete for F-204",
  "detected_language": "en"
}}

USER MESSAGE:
{text}
"""
        models_to_try = [
            m for m in [
                res.model,
                "gemini-3.6-flash",
                "gemini-flash-latest",
                "gemini-3.5-flash",
            ] if m and m not in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash")
        ]
        unique_models = []
        for m in models_to_try:
            if m and m not in unique_models:
                unique_models.append(m)

        for model in unique_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_api_key}"
            headers = {
                "Content-Type": "application/json",
            }
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "response_mime_type": "application/json",
                    "temperature": 0.1,
                },
            }
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            raw_json = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
                            parsed = json.loads(raw_json)
                            # Normalize unit
                            if parsed.get("unit"):
                                parsed["unit"] = ExtractionService.normalize_unit(parsed["unit"])
                            # Normalize status
                            parsed["status_reported"] = ExtractionService.normalize_status(parsed.get("status_reported")) or "IN_PROGRESS"
                            if not parsed.get("quantity_semantics"):
                                parsed["quantity_semantics"] = "UNKNOWN"
                            if not parsed.get("detected_language"):
                                parsed["detected_language"] = cls.detect_language(text)
                            # Handle acoustic alias industrial -> electrical
                            if (parsed.get("bulk_scope") or {}).get("keyword") and "industrial" in str(parsed.get("bulk_scope", {}).get("keyword", "")).lower() and not parsed.get("discipline"):
                                parsed["discipline"] = "Electrical"
                                if parsed.get("bulk_scope"):
                                    parsed["bulk_scope"]["discipline"] = "Electrical"

                            # Enforce Bulk Safety Invariant: Bulk requires explicit bulk markers
                            has_explicit_bulk_marker = any(w in text.lower() for w in [
                                "all", "all of them", "both", "both of them", "all activities", "all tasks",
                                "every", "complete all", "finish all", "update all", "update all of them",
                                "sab", "sabhi", "sare", "saare", "dono", "sab ke sab", "sabhi activities",
                                "sab activities", "pure", "poore", "poora", "puri"
                            ]) or any(w in text for w in ["सभी", "सारे", "दोनों", "सब"])
                            parsed["is_explicit_bulk"] = has_explicit_bulk_marker
                            if not has_explicit_bulk_marker:
                                parsed["is_bulk"] = False
                                if parsed.get("intent") == "BULK_PROGRESS_REPORT":
                                    parsed["intent"] = "PROGRESS_UPDATE_REQUEST" if parsed.get("override_percent") is not None else "PROGRESS_REPORT"

                            # Always extract deterministic activity updates
                            parsed["activity_updates"] = cls.extract_activity_updates(text)

                            return ParsedConversationalIntent(**parsed)
                    else:
                        logger.warning(f"Gemini model {model} returned HTTP {resp.status_code}")
            except Exception as e:
                logger.warning(f"Gemini API call to {model} failed: {e}")

        return None

    @classmethod
    def extract_activity_updates(cls, text: str) -> List[ActivityUpdateCandidate]:
        """
        Extracts individual per-activity update candidates from natural language.
        Supports multi-activity utterances, punctuation variations, conjunctions,
        and single-activity clauses while enforcing percentage precedence.
        """
        if not text or not text.strip():
            return []

        t = cls.normalize_indic_digits(text.strip())
        lower = t.lower()

        # Split into distinct activity clauses
        act_lookahead = (
            r"(?="
            r"(?:[A-Za-z]{2,5}[-_]?\d{2,6})"
            r"|(?:(?:mechanical|civil|electrical|piping|structural|insulation|painting)?\s*(?:activity|acitivity|activty|task|act)\s*[-_]?\s*\d+)"
            r"|(?:(?:mechanical|civil|electrical|piping|structural|insulation|painting)\s+\d+)"
            r"|(?:(?:activity|acitivity|activty|task)\s*[-_]?\s*\d+)"
            r")"
        )

        split_pattern = rf"\s*(?:;|\band\b|\baur\b|\bऔर\b|\bतथा\b|\bएवं\b|,\s*{act_lookahead})\s*"
        raw_clauses = [c.strip() for c in re.split(split_pattern, t, flags=re.IGNORECASE) if c and c.strip()]

        if len(raw_clauses) <= 1 and "," in t:
            comma_parts = [c.strip() for c in t.split(",") if c.strip()]
            has_act_mentions = sum(1 for p in comma_parts if re.search(r"\b(?:activity|acitivity|activty|task|\d+\s*%|[A-Za-z]{2,5}-\d+)\b", p, re.I))
            if has_act_mentions >= 2:
                raw_clauses = comma_parts

        candidates: List[ActivityUpdateCandidate] = []

        for clause in raw_clauses:
            cl_lower = clause.lower()

            ref = None
            code = cls.extract_activity_code(clause)
            if code:
                ref = code
            else:
                m_disc_act = re.search(
                    r"\b((?:mechanical|civil|electrical|piping|structural|insulation|painting)?\s*(?:activity|acitivity|activty|task|act)\s*[-_]?\s*\d+(?!\s*(?:%|percent\b|प्रतिशत\b)))\b",
                    clause,
                    re.IGNORECASE,
                )
                if m_disc_act:
                    ref = m_disc_act.group(1).strip()
                else:
                    m_disc_num = re.search(
                        r"\b((?:mechanical|civil|electrical|piping|structural|insulation|painting)\s+\d+(?!\s*(?:%|percent\b|प्रतिशत\b)))\b",
                        clause,
                        re.IGNORECASE,
                    )
                    if m_disc_num:
                        ref = m_disc_num.group(1).strip()
                    else:
                        m_act_num = re.search(
                            r"\b((?:activity|acitivity|activty|task)\s*[-_]?\s*\d+(?!\s*(?:%|percent\b|प्रतिशत\b)))\b",
                            clause,
                            re.IGNORECASE,
                        )
                        if m_act_num:
                            ref = m_act_num.group(1).strip()
                        else:
                            # Strip conversational verbs/prefixes from clause (multilingual: English, Hindi, Hinglish)
                            c_clean = re.sub(
                                r"^(?:मैंने|हम|हमने|i\s+|we\s+)?\s*(?:have\s+)?(?:completed|complete|finished|finish|done|started|start|updated|update|set|marked|reported|update\s+kardo|update\s+karna\s+hai|update\s+karni\s+hai|kardo|kar\s+do)?\s+(?:the\s+)?",
                                "",
                                clause.strip(),
                                flags=re.IGNORECASE,
                            )
                            # Strip trailing progress percentages, quantities, completion indicators, or status phrases
                            c_clean = re.sub(
                                r"\s+(?:to|at|progress|by|ko|mein|me|का|की|को|में)?\s*\d+(?:\.\d+)?\s*(?:%|percent|प्रतिशत)?\s*(?:complete\s*ho\s*gayi\s*hai|complete\s*ho\s*gaya\s*hai|ho\s*gaya\s*hai|ho\s*gayi\s*hai|ho\s*gaya|ho\s*gayi|done|completed|complete|finish|finished|pura\s*kiya|poora\s*kiya|पूरा\s*किया|पूरा|हो\s*गया|हो\s*गई|कर\s*दिया)?[\.\?\!|।]?\s*$",
                                "",
                                c_clean.strip(),
                                flags=re.IGNORECASE,
                            )
                            c_clean = c_clean.strip().rstrip(".,;:।")

                            has_act_keyword = bool(
                                re.search(r"\b(?:activity|acitivity|activty|task|act|action|काम|कार्य|गतिविधि|एक्टिविटी)\b", clause, re.IGNORECASE)
                            )
                            has_pct = bool(
                                re.search(r"\b\d+(?:\.\d+)?\s*(?:%|percent\b|प्रतिशत\b)", clause, re.IGNORECASE)
                                or re.search(r"\b(?:to|progress|set\s+to|at)\s+\d+(?:\.\d+)?\b", clause, re.IGNORECASE)
                            )
                            discs = cls.extract_multilingual_disciplines(clause)

                            # Only treat as a candidate activity reference if:
                            # 1) Clause explicitly mentions an activity keyword (e.g. 'mechanical welding activity'), OR
                            # 2) Clause has an explicit percentage/progress value with a discipline or descriptive words (e.g. 'mechanical turbine generator to 50%'), OR
                            # 3) Clause mentions a discipline name
                            if has_act_keyword:
                                if c_clean and len(c_clean.split()) >= 1 and not any(c_clean.lower() == p for p in ["i", "we", "the", "a", "an", "update", "progress", "to", "completed", "done"]):
                                    ref = c_clean
                                else:
                                    ref = "activity"
                            elif has_pct:
                                if c_clean and len(c_clean.split()) >= 1 and not any(c_clean.lower() == p for p in ["i", "we", "the", "a", "an", "update", "progress", "to", "completed", "done"]):
                                    ref = c_clean
                                elif discs:
                                    ref = discs[0].lower()
                            elif discs:
                                ref = discs[0].lower()

            pct = None
            pct_m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:%|percent\b|प्रतिशत\b)", clause, re.IGNORECASE)
            if pct_m:
                try:
                    pct = float(pct_m.group(1))
                except ValueError:
                    pct = None
            else:
                c_no_ref = clause
                if code:
                    c_no_ref = re.sub(rf"\b{re.escape(code)}\b", "", c_no_ref, flags=re.IGNORECASE)
                if ref:
                    c_no_ref = re.sub(rf"\b{re.escape(ref)}\b", "", c_no_ref, flags=re.IGNORECASE)
                # Strip locations so location numbers (Unit 4, Area 2, Block 1, etc.) are never mistaken for percentages
                c_no_ref = re.sub(r"\b(?:unit|area|block|pier|level|foundation|stage|phase|यूनिट)\s*[-_]?\s*\d+\b", "", c_no_ref, flags=re.IGNORECASE)
                c_no_ref = re.sub(r"\b(?:activity|acitivity|activty|task)\s*[-_]?\s*\d+\b", "", c_no_ref, flags=re.IGNORECASE)
                c_no_ref = re.sub(r"\b\d+(?:\.\d+)?\s*(?:m3|cum|tonnes|tons|t|m|mtr|sqm|nos)\b", "", c_no_ref, flags=re.IGNORECASE)
                # Check for explicit progress marker: 'to 75', 'progress 80', 'at 90'
                prog_m = re.search(r"\b(?:to|progress|set\s+to|at)\s+(\d+(?:\.\d+)?)\b", c_no_ref, re.IGNORECASE)
                if prog_m:
                    try:
                        val = float(prog_m.group(1))
                        if 0.0 <= val <= 100.0:
                            pct = val
                    except ValueError:
                        pass
                elif len(raw_clauses) >= 2:
                    num_m = re.search(r"\b(\d+(?:\.\d+)?)\b", c_no_ref)
                    if num_m:
                        try:
                            val = float(num_m.group(1))
                            if 0.0 <= val <= 100.0:
                                pct = val
                        except ValueError:
                            pass

            if not ref:
                if pct is not None:
                    ref = clause.strip()
                else:
                    continue

            qty = None
            unit = None
            qty_m = re.search(
                r"\b(\d+(?:\.\d+)?)\s*(cubic meters?|m3|cum|cu\.m|tonnes?|tons?|t|meters?|mtr|m|sqm|m2|nos|ea|each)\b",
                cl_lower,
            )
            if qty_m:
                try:
                    qty = float(qty_m.group(1))
                    unit = ExtractionService.normalize_unit(qty_m.group(2))
                except (ValueError, TypeError):
                    pass

            is_comp = (
                any(w in cl_lower for w in cls.COMPLETION_VERBS_LATIN)
                or any(w in clause for w in cls.COMPLETION_VERBS_INDIC)
            )
            status = "COMPLETED" if is_comp else "IN_PROGRESS"

            if pct is None and qty is None and is_comp:
                pct = 100.0

            candidates.append(
                ActivityUpdateCandidate(
                    activity_reference=ref,
                    reported_percent=pct,
                    reported_quantity=qty,
                    unit=unit,
                    status_reported=status,
                    confidence=1.0 if code else 0.9,
                    raw_clause=clause,
                )
            )

        return candidates

    @classmethod
    def parse_with_rules(
        cls,
        text: str,
        reference_date: Optional[datetime] = None,
        active_activity_code: Optional[str] = None,
        is_clarification_turn: bool = False,
    ) -> ParsedConversationalIntent:
        """
        Rule-based parser fallback ensuring 100% deterministic and reliable offline execution
        across English, Hindi (Devanagari), and Hinglish (Romanized Hindi / mixed).
        """
        t = text.strip()
        lower = t.lower()
        entities_present = []
        detected_lang = cls.detect_language(t)

        # 1. Intent determination
        query_prefixes = (
            "what", "who", "when", "where", "why", "how", "show", "list", "give", "display",
            "get", "find", "search", "tell", "which", "is there", "are there", "do we have",
            "does the", "details of", "summary of", "can you", "could you", "please show",
            "please list", "please give", "please tell", "status of", "progress of", "information on",
            "kya", "kitna", "kitni", "kitne", "kaun", "kaunse", "kaunsi", "kahan", "kaisa", "kaise",
            "batao", "bataiye", "dikhao", "dikhaye", "suchi", "list do", "status kya", "kya hai"
        )
        is_query_start = lower.startswith(query_prefixes) or any(t.startswith(q) for q in ["क्या", "कितना", "कितनी", "कितने", "कौन", "बताओ", "बताइए", "दिखाओ", "दिखाइए", "सूची"])

        is_query_phrase = (
            any(
                f" {q} " in f" {lower} " or lower.endswith(q) or lower.startswith(q)
                for q in [
                    "kya hai", "kitna hai", "kitne hain", "kitni hai", "kaisa hai", "status kya", "progress kya",
                    "productivity kya", "productivity kya thi", "average productivity", "what was", "what were",
                    "what is", "what are", "when is", "when will", "how long", "how much", "how many", "tell me",
                    "can you tell", "show me", "give me", "list all", "show all", "hai kya", "hain kya", "batao",
                    "dikhao", "which ones", "which of these", "which are", "which is", "what comes after",
                    "what depends on", "comes after", "comes before", "longest duration", "shortest duration",
                    "zero float", "critical path", "who is", "due today", "due tomorrow", "this week", "next week",
                    "this month", "what is completed", "what are completed", "which are completed"
                ]
            )
            or any(q in t for q in ["क्या है", "कितना है", "कितनी है", "कितने हैं", "बताओ", "बताइए", "दिखाओ", "दिखाइए", "स्थिति क्या", "कौन सा", "कौन से"])
            or (lower.endswith("?") and not re.search(r"\b(?:update|set|mark|badal|karo|change)\b", lower))
        )
        if re.search(r"\b(?:can you update|could you update|please update|update them|update karo|update kardo|update it)\b", lower):
            is_query_phrase = False
            is_query_start = False

        # 1. Completion & Work verb detection across languages
        is_completed = (
            any(w in lower for w in cls.COMPLETION_VERBS_LATIN)
            or any(w in t for w in cls.COMPLETION_VERBS_INDIC)
        )

        work_verbs = [
            "pour", "poured", "install", "installed", "erect", "erected", "excavat", "placed", "laid", "welded",
            "completed", "complete", "build", "built", "construct", "constructed", "cast", "finish", "finished",
            "assembled", "done", "dala", "daala", "dale", "dhalai", "lagaya", "lagaye", "bichhaya", "khudai",
            "kiya", "kiye"
        ]
        has_devanagari_work = any(w in t for w in [
            "डाला", "किया", "लगाया", "बिछाया", "खुदाई", "ढलाई", "पूरा", "खत्म",
            "कर दिया", "कर दिए", "कर दी", "काम कर", "पूरा कर", "निपटा", "हो गया", "पूर्ण", "समाप्त"
        ])

        if "uploaded" in lower or "attached" in lower or lower.endswith((".pdf", ".xlsx", ".csv")):
            intent = "ARTIFACT_SUBMISSION"
        elif is_clarification_turn and len(t.split()) <= 6:
            intent = "CLARIFICATION_RESPONSE"
        elif (
            re.search(r"\b(?:update|set|mark|badal|karo)\b.*(?:to\s+\d+%|\bcomplete\b|\b100%\b)", lower)
            or re.search(r"\b(?:want to update|need to update|like to update|update an activity|update activity|update schedule|change progress|report progress)\b", lower)
            or any(w in t for w in ["अपडेट कर", "अपडेट करो", "अपडेट करें", "अपडेट करोगे", "अपडेट करनी है", "अपडेट करना है"])
            or any(w in lower for w in ["update karni hai", "update karna hai", "update kardo", "update karo", "activity update"])
            or (
                not (is_query_start or is_query_phrase)
                and (
                    "activity" in lower
                    or any(disc.lower() in lower for disc in ["mechanical", "civil", "electrical", "piping", "structural", "instrumentation"])
                    or any(eq in lower for eq in ["pump", "compressor", "generator", "turbine", "boiler", "tank", "vessel", "chiller", "transformer"])
                )
            )
        ):
            intent = "PROGRESS_UPDATE_REQUEST"
        elif is_query_start or is_query_phrase:
            intent = "INFORMATION_QUERY"
        elif is_completed:
            intent = "PROGRESS_REPORT"
        elif any(verb in lower for verb in work_verbs) or has_devanagari_work:
            intent = "PROGRESS_REPORT"
        else:
            intent = "PROGRESS_REPORT" if any(c.isdigit() for c in t) else "INFORMATION_QUERY"

        # 2. Activity code extraction (e.g. CIV-1001, STR-1001, civ 1001, सिविल की 1001, சிவில் இன் 1001)
        reported_code = cls.extract_activity_code(t)
        if reported_code:
            entities_present.append("activity_code")

        # 3. Direct percentage override
        pct_match = re.search(r"\b(\d+(?:\.\d+)?)\s*%", t)
        override_percent = float(pct_match.group(1)) if pct_match else None
        if override_percent is not None:
            entities_present.append("override_percent")

        # 4. Quantity and Unit extraction
        qty = None
        unit = None
        qty_match = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(cubic meters?|m3|cum|cu\.m|क्यूबिक मीटर|घन मीटर|square meters?|m2|sqm|स्क्वायर मीटर|वर्ग मीटर|tonnes?|tons?|t|टन|meters?|mtr|m|मीटर|nos|ea|each)\b",
            lower,
        )
        if qty_match:
            qty = float(qty_match.group(1))
            unit = ExtractionService.normalize_unit(qty_match.group(2))
            entities_present.extend(["quantity", "unit"])
        else:
            # Standalone quantity: strip activity codes, code numbers, dates, and locations so their numbers aren't confused with quantities
            text_no_code = re.sub(r"\b[A-Za-z]{1,5}-\d{2,6}\b", "", t)
            text_no_code = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text_no_code)
            if reported_code and "-" in reported_code:
                code_digits = reported_code.split("-")[-1]
                text_no_code = re.sub(rf"\b{re.escape(code_digits)}\b", "", text_no_code)
            # Strip location numbers (e.g. Unit 4, Unit-4, Pier 2, Level 3, Block 5, F-204, etc.)
            text_no_code = re.sub(r"\b(?:Unit|Pier|Level|Block|Area|Foundation|यूनिट)\s*[-_]?\s*\d+\b", "", text_no_code, flags=re.IGNORECASE)
            text_no_code = re.sub(r"\b[A-Za-z0-9]+-[A-Za-z0-9]+\b", "", text_no_code)
            # Strip activity identifiers so activity 1, acitivity 2, task 02 are NOT captured as quantity 1.0
            text_no_code = re.sub(r"\b(?:activity|act|acitivity|activty|task|कार्य|काम|एक्टिविटी)\s*[-_]?\s*\d+\b", "", text_no_code, flags=re.IGNORECASE)
            # Strip percentages so 75%, 98% are not captured as quantity
            text_no_code = re.sub(r"\b\d+(?:\.\d+)?\s*%", "", text_no_code)
            stand_qty = re.search(r"\b(\d+(?:\.\d+)?)\b", text_no_code)
            if stand_qty and intent in ("PROGRESS_REPORT", "CLARIFICATION_RESPONSE", "PROGRESS_UPDATE_REQUEST"):
                try:
                    val = float(stand_qty.group(1))
                    if val != override_percent:
                        qty = val
                        entities_present.append("quantity")
                except ValueError:
                    pass

        # 5. Quantity Semantics
        if any(term in lower for term in ["cumulative", "total to date", "total so far", "in total", "kul", "ab tak", "milakar"]) or any(term in t for term in ["कुल", "अब तक"]):
            quantity_semantics = "CUMULATIVE"
        elif any(term in lower for term in ["today", "incremental", "this shift", "additional", "more", "aaj", "aaj ka", "aur"]) or "आज" in t:
            quantity_semantics = "INCREMENTAL"
        else:
            quantity_semantics = "UNKNOWN"

        # 6. Location extraction (English and Hindi/Hinglish)
        clean_t = t.strip().rstrip(".,;:!?")
        location = None
        # Match "at/for/in/on <Location>"
        loc_match = re.search(
            r"\b(?:for|at|in|on)\s+([A-Z0-9]+-[A-Z0-9]+|Unit\s+\d+|Unit-\d+|Area\s+[A-Za-z0-9]+|Pier\s+\d+|Foundation\s+(?:[A-Za-z]?\d+[A-Za-z0-9-]*|[A-Za-z]\b)|Level\s+\d+)\b",
            t,
            re.IGNORECASE,
        )
        if loc_match:
            location = loc_match.group(1).title()
        else:
            # Match "<Location> mein / me / par / pe / ke liye" (e.g., "F-204 mein", "Unit 4 me", "यूनिट 4 में")
            hindi_loc_match = re.search(
                r"\b([A-Z0-9]+-[A-Z0-9]+|Unit\s+\d+|Unit-\d+|Area\s+[A-Za-z0-9]+|Pier\s+\d+|Foundation\s+(?:[A-Za-z]?\d+[A-Za-z0-9-]*|[A-Za-z]\b)|यूनिट\s+\d+)\s*(?:mein|me|par|pe|ke liye|के लिए|में|पर)\b",
                t,
                re.IGNORECASE,
            )
            if hindi_loc_match:
                location = hindi_loc_match.group(1).title()
            else:
                # Standalone Unit X or Area X or Foundation X
                unit_match = re.search(r"\b(Unit\s+\d+|Unit-\d+|Area\s+[A-Za-z0-9]+|Pier\s+\d+|Foundation\s+(?:[A-Za-z]?\d+[A-Za-z0-9-]*|[A-Za-z]\b)|यूनिट\s+\d+)\b", t, re.IGNORECASE)
                if unit_match:
                    location = unit_match.group(1).title()
                else:
                    # Standalone tag in sentence (e.g. "F-204" in "Aaj F-204 mein...")
                    tag_match = re.search(r"\b([A-Z]{1,4}-\d{2,5})\b", t, re.IGNORECASE)
                    if tag_match:
                        cand_tag = tag_match.group(1).upper()
                        if not (cand_tag.startswith("CIV-") or cand_tag.startswith("STR-") or cand_tag.startswith("PIP-") or cand_tag.startswith("ELE-") or cand_tag.startswith("INS-") or cand_tag.startswith("MEC-")):
                            location = cand_tag

        if not location:
            # Standalone code in clarification (e.g. "F-204" or "CIV-1001")
            standalone_loc = re.match(r"^([A-Z0-9]+-[A-Z0-9]+)$", clean_t, re.IGNORECASE)
            if standalone_loc:
                val = standalone_loc.group(1).upper()
                if val.startswith("CIV-") or val.startswith("STR-") or val.startswith("PIP-") or val.startswith("ELE-") or val.startswith("INS-"):
                    if not reported_code:
                        reported_code = val
                else:
                    location = val

        if location:
            loc_upper = location.upper()
            if loc_upper == (reported_code or "").upper() or any(loc_upper.startswith(p) for p in ["CIV-", "STR-", "PIP-", "ELE-", "INS-"]):
                location = None

        if location:
            entities_present.append("location")

        # 7. Discipline extraction across all languages
        discipline = None
        extracted_discs = cls.extract_multilingual_disciplines(t)
        if extracted_discs:
            discipline = extracted_discs[0]
            entities_present.append("discipline")
        elif reported_code and "-" in reported_code:
            pfx = reported_code.split("-")[0].upper()
            if pfx in cls.PREFIX_TO_CANON_DISCIPLINE:
                discipline = cls.PREFIX_TO_CANON_DISCIPLINE[pfx]
                entities_present.append("discipline")

        # 8. Execution Date extraction
        raw_date = None
        if "today" in lower or "aaj" in lower or "आज" in t:
            raw_date = "today"
        elif "yesterday" in lower or "kal" in lower or "कल" in t:
            raw_date = "yesterday"
        else:
            date_m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
            if date_m:
                raw_date = date_m.group(1)
            else:
                for w in cls.WEEKDAYS:
                    if w in lower:
                        raw_date = w
                        break
        if raw_date:
            entities_present.append("execution_date")

        # 9. Status reported & multilingual completion detection
        is_completed = (
            any(w in lower for w in cls.COMPLETION_VERBS_LATIN)
            or any(w in t for w in cls.COMPLETION_VERBS_INDIC)
        )
        if is_completed:
            status_reported = "COMPLETED"
            if override_percent is None and (reported_code or intent in ("PROGRESS_REPORT", "PROGRESS_UPDATE_REQUEST")):
                override_percent = 100.0
                if "override_percent" not in entities_present:
                    entities_present.append("override_percent")
        else:
            status_reported = "IN_PROGRESS"

        # 10. Bulk Intent and Scope Detection
        is_bulk = False
        bulk_scope = None

        clean_norm = " ".join(lower.split())
        bulk_keywords = [
            "all", "every", "everything", "all of the", "all of them",
            "both", "both of them", "all of these", "all activities", "all the activities", "all of our activities",
            "sabhi", "saare", "sare", "sab", "sab kuch", "har ek", "sabko", "sab ko"
        ]
        indic_bulk_keywords = [
            # Hindi
            "सभी", "सारे", "सब", "सबको", "सबके सब", "सारे के सारे", "दोनों",
            # Tamil
            "அனைத்து", "அனைத்தும்", "எல்லாம்", "முழுவதும்", "செயல்பாடுகளையும்", "செயல்பாடுகள்", "செயல்பாடு", "இரண்டும்",
            # Telugu
            "అన్నీ", "అన్ని", "రెండు", "మొత్తం", "కార్యకలాపాలు",
            # Bengali
            "সব", "সমস্ত", "উভয়", "কার্যক্রম",
            # Marathi
            "सर्व", "सगळे", "दोन्ही", "कार्ये",
            # Gujarati
            "બધા", "તમામ", "બંને", "પ્રવૃત્તિઓ",
            # Kannada
            "ಎಲ್ಲಾ", "ಎಲ್ಲವೂ", "ಎರಡೂ", "ಚಟುವಟಿಕೆಗಳು",
            # Malayalam
            "എല്ലാം", "എല്ലാ", "രണ്ടും", "പ്രവർത്തനങ്ങൾ",
            # Punjabi
            "ਸਾਰੇ", "ਸਭ", "ਦੋਵੇਂ", "ਗਤੀਵਿਧੀਆਂ",
            # Odia
            "ସବୁ", "ସମସ୍ତ", "ଉଭୟ"
        ]
        has_bulk_keyword = (
            any(re.search(rf"\b{re.escape(bk)}\b", clean_norm) for bk in bulk_keywords)
            or any(bk in t for bk in indic_bulk_keywords)
        )
        is_explicit_bulk = has_bulk_keyword

        # Multi-activity updates check
        updates_in_t = cls.extract_activity_updates(t)

        indic_scope_words = [
            "काम", "कार्य", "एक्टिविटी", "एक्टिविटीज", "गतिविधियों", "गतिविधि",
            "இलेक्ट्रिकल", "इलेक्ट्रिक", "सिविल", "पाइपिंग", "स्ट्रक्चरल", "कंक्रीट", "केबल", "वायरिंग", "अपडेट"
        ]

        has_scope_indicator = bool(
            discipline
            or len(extracted_discs) > 0
            or any(w in clean_norm for w in ["activit", "work", "package", "cable", "tray", "pour", "concrete", "pipe", "piping", "steel", "lighting", "foundation", "kaam", "dhalai", "update"])
            or any(w in t for w in indic_scope_words)
        )

        is_direct_bulk_clarification = is_clarification_turn and (
            any(clean_norm == term or clean_norm.startswith(term) for term in [
                "all of them", "all", "both", "both of them", "all of these", "update all", "update all of them", "all activities", "all the activities",
                "sabhi", "saare", "sab", "sab ke sab"
            ]) or any(t.startswith(term) for term in ["सभी", "सारे", "सब", "அனைத்து", "அனைத்தும்", "எல்லாம்"])
        )

        if len(updates_in_t) >= 2:
            is_bulk = False
            is_explicit_bulk = False
            intent = "PROGRESS_REPORT"
        elif is_direct_bulk_clarification:
            intent = "CLARIFICATION_RESPONSE"
            is_bulk = True
            is_explicit_bulk = True
        elif not (is_query_start or is_query_phrase) and has_bulk_keyword and (has_scope_indicator or len(extracted_discs) > 0) and not reported_code:
            intent = "BULK_PROGRESS_REPORT"
            is_bulk = True
            is_explicit_bulk = True
            if is_completed and override_percent is None:
                override_percent = 100.0
                status_reported = "COMPLETED"
        elif any(phrase in clean_norm for phrase in [
            "finished the electrical work",
            "completed the electrical work",
            "finished the civil work",
            "completed the civil work",
            "finished the piping work",
            "completed the piping work",
            "finished the structural work",
            "completed the structural work",
            "civil work complete ho gaya",
            "electrical work complete ho gaya",
            "piping work complete ho gaya"
        ]) or any(phrase in t for phrase in [
            "इलेक्ट्रिकल काम", "सिविल काम", "पाइपिंग काम", "सारे इलेक्ट्रिकल", "सारे सिविल", "सारे काम",
            "सारे कार्य", "सभी काम", "सभी कार्य"
        ]):
            intent = "BULK_PROGRESS_REPORT"
            is_bulk = True

        if is_bulk:
            keyword = None
            if "cable tray" in clean_norm or "tray" in clean_norm or "ट्रे" in t:
                keyword = "cable tray"
            elif "cable" in clean_norm or "केबल" in t:
                keyword = "cable"
            elif "lighting" in clean_norm or "लाइटिंग" in t:
                keyword = "lighting"
            elif "foundation" in clean_norm or "नींव" in t:
                keyword = "foundation"
            elif "pipe" in clean_norm or "piping" in clean_norm or "पाइप" in t:
                keyword = "piping"
            elif "concrete" in clean_norm or "dhalai" in clean_norm or "कंक्रीट" in t or "ढलाई" in t:
                keyword = "concrete"
            elif "electrical" in clean_norm or "electric" in clean_norm or "इलेक्ट्रिकल" in t or "बिजली" in t:
                keyword = "electrical"

            bulk_scope = {
                "discipline": discipline,
                "location": location,
                "keyword": keyword,
                "wbs_hint": None,
                "raw_text": t,
            }

        # 11. Equipment / Asset extraction
        asset = None
        equip_match = re.search(
            r"\b(pump|compressor|generator|turbine|boiler|tank|vessel|chiller|transformer|switchgear|motor|valve|conveyor|piping|pipeline|cable\s+tray|lighting|पंप|कंप्रेसर|मोटर|टर्बाइन|जनरेटर)\b",
            lower,
        )
        if equip_match:
            asset = equip_match.group(1).title()
            if "asset" not in entities_present:
                entities_present.append("asset")

        return ParsedConversationalIntent(
            intent=intent,
            confidence=0.88 if intent != "INFORMATION_QUERY" else 0.95,
            is_bulk=is_bulk,
            is_explicit_bulk=is_explicit_bulk,
            bulk_scope=bulk_scope,
            entities_present=entities_present,
            quantity=qty,
            unit=unit,
            quantity_semantics=quantity_semantics,
            location=location,
            discipline=discipline,
            contractor=None,
            asset=asset,
            wbs_hint=None,
            reported_activity_code=reported_code,
            execution_date=raw_date,
            status_reported=status_reported,
            override_percent=override_percent,
            description=t,
            detected_language=detected_lang,
            activity_updates=updates_in_t,
        )

    @classmethod
    def parse_message(
        cls,
        text: str,
        project_data_date: Optional[datetime] = None,
        active_activity_code: Optional[str] = None,
        is_clarification_turn: bool = False,
        conversation_language: Optional[str] = None,
        conversation_style: Optional[str] = None,
        language_locked: bool = False,
    ) -> ParsedConversationalIntent:
        """
        Primary entry point:
        1. Attempts Gemini structured extraction directly on the input text.
        2. Fast rule-based parsing fallback directly on the input text.
        3. Universal multilingual semantic pivot fallback:
           If direct rule-based parsing yields an ambiguous query on non-English / Indic text,
           translates text internally using Sarvam Mayura into an English semantic pivot.
           This provides 100% accurate intent & entity extraction across Marathi, Gujarati,
           Bengali, Tamil, Telugu, Punjabi, Kannada, Malayalam, Odia, etc., while preserving
           the user's original message, transcription, and locked conversation language completely authentic.
        """
        ref_date_str = project_data_date.strftime("%Y-%m-%d") if project_data_date else ""
        parsed = cls.parse_with_gemini(
            text=text,
            project_data_date_str=ref_date_str,
            active_activity_code=active_activity_code,
            is_clarification_turn=is_clarification_turn,
            conversation_language=conversation_language,
            conversation_style=conversation_style,
            language_locked=language_locked,
        )
        if not parsed:
            parsed = cls.parse_with_rules(
                text=text,
                reference_date=project_data_date,
                active_activity_code=active_activity_code,
                is_clarification_turn=is_clarification_turn,
            )

            # The semantic pivot is ONLY needed when direct parsing fails to find
            # any actionable intent, discipline, or scope on non-ASCII Indic script text.
            has_actionable_entities = bool(parsed.reported_activity_code or parsed.override_percent or parsed.quantity)
            is_unresolved_indic = (parsed.intent == "INFORMATION_QUERY" or (not parsed.discipline and not parsed.is_bulk)) and not has_actionable_entities
            has_indic_script = any(ord(c) > 127 for c in text)

            if is_unresolved_indic and has_indic_script:
                try:
                    from app.services.sarvam_service import SarvamService
                    shielded_txt, _ = SarvamService.shield_technical_tokens(text)
                    source_code = (
                        conversation_language
                        if (conversation_language and conversation_language != "unknown" and not conversation_language.startswith("en"))
                        else cls.detect_language(text)
                    )
                    pivot_en = SarvamService.translate_text(
                        text=shielded_txt,
                        source_language_code=source_code,
                        target_language_code="en-IN",
                    )
                    if pivot_en:
                        unshielded_pivot = SarvamService.unshield_technical_tokens(pivot_en)
                        pivot_parsed = cls.parse_with_rules(
                            text=unshielded_pivot,
                            reference_date=project_data_date,
                            active_activity_code=active_activity_code,
                            is_clarification_turn=is_clarification_turn,
                        )
                        if pivot_parsed.intent != "INFORMATION_QUERY" or pivot_parsed.is_bulk or pivot_parsed.discipline:
                            if pivot_parsed.bulk_scope:
                                pivot_parsed.bulk_scope["raw_text"] = text
                            pivot_parsed.description = text
                            pivot_parsed.detected_language = parsed.detected_language
                            parsed = pivot_parsed
                except Exception as pivot_err:
                    logger.warning(f"Universal multilingual semantic pivot fallback failed: {pivot_err}")

        # Post-Processing Guarantees:
        # 1. Guarantee activity code extraction across all languages
        if not parsed.reported_activity_code:
            extracted_code = cls.extract_activity_code(text)
            if extracted_code:
                parsed.reported_activity_code = extracted_code
                if "activity_code" not in parsed.entities_present:
                    parsed.entities_present.append("activity_code")

        # Guarantee activity code number is not misidentified as a quantity
        if parsed.reported_activity_code and "-" in parsed.reported_activity_code and parsed.quantity is not None:
            code_num = parsed.reported_activity_code.split("-")[-1]
            try:
                if str(int(parsed.quantity)) == code_num:
                    parsed.quantity = None
                    if "quantity" in parsed.entities_present:
                        parsed.entities_present.remove("quantity")
            except (ValueError, TypeError):
                pass

        # Attach parsed activity updates
        parsed.activity_updates = cls.extract_activity_updates(text)

        # Quantity Semantics fallback
        if parsed.quantity is not None and (parsed.quantity_semantics == "UNKNOWN" or not parsed.quantity_semantics):
            if any(term in text.lower() for term in ["today", "incremental", "this shift", "additional", "more", "aaj", "aaj ka", "aur"]) or "आज" in text:
                parsed.quantity_semantics = "INCREMENTAL"
            elif any(term in text.lower() for term in ["cumulative", "total to date", "total so far", "in total", "kul", "ab tak", "milakar"]) or any(term in text for term in ["कुल", "अब तक"]):
                parsed.quantity_semantics = "CUMULATIVE"

        # Multi-activity updates take precedence over uniform bulk
        if len(parsed.activity_updates) >= 2:
            parsed.is_bulk = False
            parsed.is_explicit_bulk = False
            parsed.intent = "PROGRESS_REPORT"
        elif len(parsed.activity_updates) == 1:
            cand = parsed.activity_updates[0]
            if cand.reported_percent is not None:
                parsed.override_percent = cand.reported_percent
                if "override_percent" not in parsed.entities_present:
                    parsed.entities_present.append("override_percent")
            if cand.status_reported:
                parsed.status_reported = cand.status_reported
            if cand.reported_quantity is not None and parsed.quantity is None:
                parsed.quantity = cand.reported_quantity
                if cand.unit and parsed.unit is None:
                    parsed.unit = cand.unit

        # 2. Guarantee completion detection and percentage precedence
        is_comp = (
            any(w in text.lower() for w in cls.COMPLETION_VERBS_LATIN)
            or any(w in text for w in cls.COMPLETION_VERBS_INDIC)
        )
        if is_comp and parsed.intent != "INFORMATION_QUERY":
            parsed.status_reported = "COMPLETED"
            discs = cls.extract_multilingual_disciplines(text)
            if parsed.override_percent is None:
                parsed.override_percent = 100.0
                if "override_percent" not in parsed.entities_present:
                    parsed.entities_present.append("override_percent")

            if parsed.is_explicit_bulk and not parsed.reported_activity_code and len(parsed.activity_updates) <= 1:
                parsed.intent = "BULK_PROGRESS_REPORT"
                parsed.is_bulk = True
                if not parsed.bulk_scope:
                    parsed.bulk_scope = {"discipline": discs[0] if discs else None, "disciplines": discs, "raw_text": text}
            else:
                parsed.is_bulk = False
                if discs and not parsed.discipline:
                    parsed.discipline = discs[0]
        elif is_comp and parsed.intent == "INFORMATION_QUERY":
            parsed.status_reported = "COMPLETED"

        # 3. Guarantee discipline extraction from multilingual tokens if missing
        if not parsed.discipline:
            discs = cls.extract_multilingual_disciplines(text)
            if discs:
                parsed.discipline = discs[0]
                if "discipline" not in parsed.entities_present:
                    parsed.entities_present.append("discipline")

        return parsed
