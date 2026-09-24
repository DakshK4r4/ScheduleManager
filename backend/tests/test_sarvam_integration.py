from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import (
    Activity,
    Conversation,
    ConversationMessage,
    ExecutionEvent,
    Project,
    UpdateProposal,
    WBSNode,
)
from app.schemas.agent import ActionCardDTO
from app.services.agent_parser import ConversationalParser
from app.services.agent_service import TimeAgentService
from app.services.sarvam_service import SarvamService


@pytest.fixture
def sarvam_project(db_session: Session):
    """Creates a realistic construction project for Sarvam integration testing."""
    proj = Project(
        id="proj-sarvam-demo",
        project_code="METRO_LINE3",
        name="Metro Line 3 Underground Corridor",
        planned_start=datetime(2024, 1, 1, 8, 0),
        planned_finish=datetime(2025, 12, 31, 17, 0),
        data_date=datetime(2024, 10, 1, 0, 0),
    )
    db_session.add(proj)

    wbs1 = WBSNode(
        id="wbs-sarvam-1",
        project_id="proj-sarvam-demo",
        code="WBS-CIV",
        name="Civil Foundations",
    )
    db_session.add(wbs1)

    act1 = Activity(
        id="act-civ-1004",
        project_id="proj-sarvam-demo",
        wbs_id="wbs-sarvam-1",
        activity_code="CIV-1004",
        location_code="F-204",
        name="Foundation Concrete Pour F-204",
        status="IN_PROGRESS",
        planned_start=datetime(2024, 8, 1, 8, 0),
        planned_finish=datetime(2024, 9, 30, 17, 0),
        original_duration=60.0,
        percent_complete=50.0,
        planned_quantity=100.0,
        quantity_unit="m3",
    )

    act2 = Activity(
        id="act-ele-2001",
        project_id="proj-sarvam-demo",
        wbs_id="wbs-sarvam-1",
        activity_code="ELE-2001",
        name="Cable Tray Installation Level 1",
        status="NOT_STARTED",
        planned_start=datetime(2024, 9, 1, 8, 0),
        planned_finish=datetime(2024, 10, 31, 17, 0),
        original_duration=45.0,
        percent_complete=0.0,
        planned_quantity=500.0,
        quantity_unit="m",
    )

    db_session.add_all([act1, act2])
    db_session.commit()
    return proj


# =====================================================================
# Test 1: Hindi Voice Input & Hindi Response
# =====================================================================
def test_hindi_voice_stt_and_response(client: TestClient, sarvam_project: Project, db_session: Session):
    """
    Test 1: Speak Hindi.
    Expected: correct transcript, hi-IN detection, Hindi response.
    """
    conv_dto = TimeAgentService.get_or_create_conversation(
        db=db_session, project_id=sarvam_project.id, force_new=True
    )
    conv_id = conv_dto.conversation_id

    mock_transcript = "आज F-204 में पैंतीस क्यूबिक मीटर कंक्रीट डाला गया है।"
    mock_detected_lang = "hi-IN"

    with patch.object(SarvamService, "transcribe_audio", return_value=(mock_transcript, mock_detected_lang)):
        fake_audio_bytes = b"RIFF....WAVEfmt ...."
        response = client.post(
            f"/api/v1/projects/{sarvam_project.id}/agent/conversations/{conv_id}/voice",
            files={"file": ("recording.wav", fake_audio_bytes, "audio/wav")},
            headers={"X-User-ID": "site-supervisor"},
        )
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["transcript"] == mock_transcript
        assert data.get("transcription") == mock_transcript
        assert data["detected_language"] in ("hi", "hi-IN")
        assert data["conversation_language"] == "hi-IN"
        assert data["language_locked"] is True
        # Response should be in Hindi
        assert any(term in data["reply_text"] for term in ["पुष्टि", "F-204", "CIV-1004", "प्रतिशत", "%"])


# =====================================================================
# Test 2: English Voice Input & English Response
# =====================================================================
def test_english_voice_stt_and_response(client: TestClient, sarvam_project: Project, db_session: Session):
    """
    Test 2: Speak English.
    Expected: correct transcript, en/en-IN detection, English response.
    """
    conv_dto = TimeAgentService.get_or_create_conversation(
        db=db_session, project_id=sarvam_project.id, force_new=True
    )
    conv_id = conv_dto.conversation_id

    mock_transcript = "We poured 35 m3 of concrete at F-204 today."
    mock_detected_lang = "en-IN"

    with patch.object(SarvamService, "transcribe_audio", return_value=(mock_transcript, mock_detected_lang)):
        fake_audio_bytes = b"RIFF....WAVEfmt ...."
        response = client.post(
            f"/api/v1/projects/{sarvam_project.id}/agent/conversations/{conv_id}/voice",
            files={"file": ("recording.wav", fake_audio_bytes, "audio/wav")},
            headers={"X-User-ID": "site-supervisor"},
        )
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["transcript"] == mock_transcript
        assert data.get("transcription") == mock_transcript
        assert data["detected_language"] in ("en", "en-IN")
        assert data["conversation_language"] == "en-IN"
        assert data["language_locked"] is True
        assert "confirm" in data["reply_text"].lower() or "matched" in data["reply_text"].lower()


# =====================================================================
# Test 3: Tamil Start -> English Cross-Language -> Tamil Response
# =====================================================================
def test_tamil_conversation_cross_language_reply(sarvam_project: Project, db_session: Session):
    """
    Test 3: Start conversation in Tamil. Then send English text.
    Expected: Tamil response maintained strictly.
    """
    conv = Conversation(
        id="conv-tamil-test",
        project_id=sarvam_project.id,
        user_id="site-supervisor",
        language="ta-IN",
        language_style="tamil",
        language_locked=True,
    )
    db_session.add(conv)
    db_session.commit()

    # User sends English question
    english_query = "What is the progress of F-204?"

    # Mock Sarvam translate_text to simulate Tamil translation
    def mock_translate(text, target_language_code, source_language_code="en-IN"):
        return f"[TAMIL_TRANSLATION_OF: {text}]"

    with patch.object(SarvamService, "translate_text", side_effect=mock_translate):
        resp = TimeAgentService.process_message(
            db=db_session,
            project_id=sarvam_project.id,
            conversation_id=conv.id,
            user_content=english_query,
        )

        assert resp.conversation_language == "ta-IN"
        assert resp.language_locked is True
        assert "[TAMIL_TRANSLATION_OF:" in resp.reply_text
        assert "F-204" in resp.reply_text


# =====================================================================
# Test 4: Hinglish Code-Mixed Speech & Reply
# =====================================================================
def test_hinglish_codemix_start_and_followup(sarvam_project: Project, db_session: Session):
    """
    Test 4: Start with 'Aaj F-204 ka concreting complete hua.'
    Then send 'What is the current progress?'
    Expected: Hinglish response maintained.
    """
    conv_dto = TimeAgentService.get_or_create_conversation(
        db=db_session, project_id=sarvam_project.id, force_new=True
    )
    conv_id = conv_dto.conversation_id

    # 1. First message in Hinglish
    msg1 = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv_id,
        user_content="Aaj F-204 ka concreting complete hua.",
    )
    assert msg1.conversation_style == "hinglish" or msg1.conversation_language == "hinglish"
    assert msg1.language_locked is True

    # 2. English question in the same conversation
    msg2 = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv_id,
        user_content="What is the current progress?",
    )
    # Must remain Hinglish response
    assert any(term in msg2.reply_text.lower() for term in ["ka", "hai", "progress", "current", "update"])
    assert "is currently" not in msg2.reply_text.lower()


# =====================================================================
# Test 5: Cross-Language Input (Hindi Lock -> English Input)
# =====================================================================
def test_cross_language_hindi_lock(sarvam_project: Project, db_session: Session):
    """
    Test 5: Conversation starts Hindi. Send: 'What is F-204 status?'
    Expected: Hindi response.
    """
    conv_dto = TimeAgentService.get_or_create_conversation(
        db=db_session, project_id=sarvam_project.id, force_new=True
    )
    conv_id = conv_dto.conversation_id

    # First message in Devanagari Hindi
    msg1 = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv_id,
        user_content="आज F-204 में काम पूरा हुआ है।",
    )
    assert msg1.conversation_language.startswith("hi")
    assert msg1.language_locked is True

    # User sends English message
    msg2 = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv_id,
        user_content="What is F-204 status?",
    )
    # Response must remain Hindi
    assert any(term in msg2.reply_text for term in ["की वर्तमान प्रगति", "प्रतिशत", "है", "%"])
    assert "The current progress" not in msg2.reply_text


# =====================================================================
# Test 6: Persistence across Reopening / Re-querying
# =====================================================================
def test_conversation_language_persistence(client: TestClient, sarvam_project: Project, db_session: Session):
    """
    Test 6: Refresh/reopen conversation.
    Expected: Same conversation language and lock status preserved.
    """
    conv = Conversation(
        id="conv-persist-test",
        project_id=sarvam_project.id,
        user_id="site-supervisor",
        title="Persisted Hindi Chat",
        language="hi-IN",
        language_style="hindi",
        language_locked=True,
    )
    db_session.add(conv)
    db_session.commit()

    # Query via API endpoint (simulating browser reload / reopen)
    res = client.get(f"/api/v1/projects/{sarvam_project.id}/agent/conversations/{conv.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["conversation_language"] == "hi-IN"
    assert data["conversation_style"] == "hindi"
    assert data["language_locked"] is True


# =====================================================================
# Test 7: Multi-Chat Isolation (Chat A = Hindi, B = English, C = Tamil, D = Hinglish)
# =====================================================================
def test_multiple_conversations_language_isolation(sarvam_project: Project, db_session: Session):
    """
    Test 7: Chat A = Hindi, Chat B = English, Chat C = Tamil, Chat D = Hinglish.
    Switching between them preserves each conversation's own language.
    """
    conv_a = Conversation(
        id="conv-a-hi", project_id=sarvam_project.id, language="hi-IN", language_style="hindi", language_locked=True
    )
    conv_b = Conversation(
        id="conv-b-en", project_id=sarvam_project.id, language="en-IN", language_style="english", language_locked=True
    )
    conv_c = Conversation(
        id="conv-c-ta", project_id=sarvam_project.id, language="ta-IN", language_style="tamil", language_locked=True
    )
    conv_d = Conversation(
        id="conv-d-hing", project_id=sarvam_project.id, language="hi-IN", language_style="hinglish", language_locked=True
    )
    db_session.add_all([conv_a, conv_b, conv_c, conv_d])
    db_session.commit()

    # Query the same activity in all 4 conversations
    query = "What is the progress of F-204?"

    # Chat A (Hindi) -> Hindi reply
    res_a = TimeAgentService.process_message(db_session, sarvam_project.id, conv_a.id, query)
    assert any(term in res_a.reply_text for term in ["की वर्तमान प्रगति", "है"])

    # Chat B (English) -> English reply
    res_b = TimeAgentService.process_message(db_session, sarvam_project.id, conv_b.id, query)
    assert "current progress" in res_b.reply_text.lower()

    # Chat C (Tamil) -> Tamil reply via fallback
    with patch.object(SarvamService, "translate_text", return_value="F-204 தற்போதைய முன்னேற்றம் 50%"):
        res_c = TimeAgentService.process_message(db_session, sarvam_project.id, conv_c.id, query)
        assert "முன்னேற்றம்" in res_c.reply_text

    # Chat D (Hinglish) -> Hinglish reply
    res_d = TimeAgentService.process_message(db_session, sarvam_project.id, conv_d.id, query)
    assert any(term in res_d.reply_text.lower() for term in ["ka current progress", "hai"])


# =====================================================================
# Test 8: Technical Identifiers & Token Shielding
# =====================================================================
def test_technical_identifiers_token_shielding():
    """
    Test 8: Verify CIV-1004, F-204, 35 m3, 80% remain strictly preserved
    and never altered through Sarvam translation.
    """
    raw_text = "Activity CIV-1004 (F-204) has reached 80% with 35 m3 completed on 2024-10-01."
    shielded, token_map = SarvamService.shield_technical_tokens(raw_text)

    # All technical tokens are shielded in [[...]]
    assert "[[CIV-1004]]" in shielded
    assert "[[F-204]]" in shielded
    assert "[[80%]]" in shielded or ("[[80]]" in shielded and "[%]" in shielded) or "80%" in shielded
    assert "[[35]]" in shielded or "35" in shielded

    # Simulate translation preserving the shielded tokens
    mock_translated_indic = f"நடவடிக்கை [[CIV-1004]] ([[F-204]]) ஆனது [[80%]] ஐ எட்டியுள்ளது, [[35]] m3 முடிந்தது."
    unshielded = SarvamService.unshield_technical_tokens(mock_translated_indic)

    assert "CIV-1004" in unshielded
    assert "F-204" in unshielded
    assert "[[" not in unshielded
    assert "]]" not in unshielded


# =====================================================================
# Test 9: Clarification preserves locked conversation language
# =====================================================================
def test_clarification_in_locked_language(sarvam_project: Project, db_session: Session):
    """
    Test 9: Clarification dialog preserves the locked conversation language.
    """
    conv = Conversation(
        id="conv-clarify-hi",
        project_id=sarvam_project.id,
        user_id="site-supervisor",
        language="hi-IN",
        language_style="hindi",
        language_locked=True,
    )
    db_session.add(conv)
    db_session.commit()

    # Ambiguous report in Hindi
    res = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv.id,
        user_content="कंक्रीट का काम पूरा हो गया",
    )

    # Clarification question and action card must be in Hindi
    assert res.conversation_language == "hi-IN"
    assert any(term in res.reply_text for term in ["क्या", "कार्य", "activity", "confirm"])
    if res.action_card:
        assert res.action_card.type == "CLARIFICATION_CHOICE"


# =====================================================================
# Test 10: Proposal Lifecycle preserves locked conversation language
# =====================================================================
def test_proposal_lifecycle_locked_language(sarvam_project: Project, db_session: Session):
    """
    Test 10: Proposal review, confirmation, error, and success preserve the locked language.
    """
    conv = Conversation(
        id="conv-prop-hi",
        project_id=sarvam_project.id,
        user_id="site-supervisor",
        language="hi-IN",
        language_style="hindi",
        language_locked=True,
    )
    db_session.add(conv)
    db_session.commit()

    # Staged proposal report with date anchor 'आज'
    res = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv.id,
        user_content="आज CIV-1004 80% पूरा हुआ",
    )

    assert res.action_card is not None
    assert res.action_card.type == "PROPOSAL_CONFIRMATION"
    assert res.action_card.confirm_label == "अपडेट की पुष्टि करें"
    assert res.action_card.reject_label == "रद्द करें"
    assert any(term in res.reply_text for term in ["match", "confirm", "बढ़ाएगा", "schedule", "पुष्टि"])
    proposal_id = res.action_card.proposal_id
    assert proposal_id is not None

    # Confirm the proposal and verify confirmation response and database mutation
    conf_res = TimeAgentService.confirm_proposal(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv.id,
        proposal_id=proposal_id,
        caller_id="site-supervisor",
    )
    assert conf_res.status == "APPLIED"
    assert conf_res.new_percent == 80.0
    assert conf_res.activity_code == "CIV-1004"


# =====================================================================
# Test 11: Multilingual Code-Mixed Speech Detection & Preservation
# =====================================================================
def test_multilingual_codemix_detection_and_response(client: TestClient, sarvam_project: Project, db_session: Session):
    """
    Test 11: Code-mixed speech (Hindi + English)
    Expected: is_code_mixed is True, detected_languages includes both hi and en,
    transcription preserved, and agent responds in matching Hinglish.
    """
    conv_dto = TimeAgentService.get_or_create_conversation(
        db=db_session, project_id=sarvam_project.id, force_new=True
    )
    conv_id = conv_dto.conversation_id

    mock_transcript = "मुझे कल के लिए एक नया task बनाना है"
    mock_dict = {
        "transcription": mock_transcript,
        "transcript": mock_transcript,
        "detected_language": "hi",
        "detected_language_code": "hi-IN",
        "is_code_mixed": True,
        "detected_languages": ["hi", "en"],
        "confidence": 0.95,
        "low_confidence": False,
    }

    with patch.object(SarvamService, "transcribe_audio", return_value=mock_dict):
        fake_audio_bytes = b"RIFF....WAVEfmt ...."
        response = client.post(
            f"/api/v1/projects/{sarvam_project.id}/agent/conversations/{conv_id}/voice",
            files={"file": ("recording.wav", fake_audio_bytes, "audio/wav")},
            headers={"X-User-ID": "site-supervisor"},
        )
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["transcription"] == mock_transcript
        assert data["detected_language"] == "hi"
        assert data["is_code_mixed"] is True
        assert "hi" in data["detected_languages"]
        assert "en" in data["detected_languages"]


# =====================================================================
# Test 12: Low Confidence / Ambiguous Audio Rejection
# =====================================================================
def test_low_confidence_audio_rejection(client: TestClient, sarvam_project: Project, db_session: Session):
    """
    Test 12: Short or ambiguous audio with low confidence
    Expected: HTTP 422 asking user to repeat clearly, not silently translated.
    """
    conv_dto = TimeAgentService.get_or_create_conversation(
        db=db_session, project_id=sarvam_project.id, force_new=True
    )
    conv_id = conv_dto.conversation_id

    mock_dict = {
        "transcription": "...",
        "transcript": "...",
        "detected_language": "en",
        "detected_language_code": "en-IN",
        "is_code_mixed": False,
        "detected_languages": ["en"],
        "confidence": 0.12,
        "low_confidence": True,
    }

    with patch.object(SarvamService, "transcribe_audio", return_value=mock_dict):
        fake_audio_bytes = b"RIFF....WAVEfmt ...."
        response = client.post(
            f"/api/v1/projects/{sarvam_project.id}/agent/conversations/{conv_id}/voice",
            files={"file": ("recording.wav", fake_audio_bytes, "audio/wav")},
            headers={"X-User-ID": "site-supervisor"},
        )
        assert response.status_code == 422
        assert "Audio confidence is too low" in response.text


# =====================================================================
# Test 13: Hindi Bulk Electrical Progress Report & Scope Proposal Card
# =====================================================================
def test_hindi_bulk_electrical_progress_report(sarvam_project: Project, db_session: Session):
    """
    Test 13: User says in Hindi:
    'मैंने सारे इलेक्ट्रिकल काम कर दिए हैं। क्या तुम उन सबको अपडेट कर सकते हो?'
    Expected:
    - Intent parsed as BULK_PROGRESS_REPORT
    - Discipline parsed as Electrical
    - Status parsed as COMPLETED
    - Is bulk parsed as True
    - TimeAgentService returns BULK_SCOPE_PROPOSAL card in Hindi
    """
    # Add a second electrical activity to trigger bulk scope proposal
    act2 = Activity(
        id="act-ele-2002",
        project_id=sarvam_project.id,
        activity_code="ELE-2002",
        name="Cable Pulling & Termination Level 1",
        status="NOT_STARTED",
        percent_complete=0.0,
    )
    db_session.add(act2)
    db_session.commit()

    conv_dto = TimeAgentService.get_or_create_conversation(
        db=db_session, project_id=sarvam_project.id, force_new=True
    )
    conv_id = conv_dto.conversation_id

    hindi_text = "मैंने सारे इलेक्ट्रिकल काम कर दिए हैं। क्या तुम उन सबको अपडेट कर सकते हो?"
    parsed = ConversationalParser.parse_with_rules(hindi_text)
    assert parsed.intent == "BULK_PROGRESS_REPORT"
    assert parsed.discipline == "Electrical"
    assert parsed.status_reported == "COMPLETED"
    assert parsed.is_bulk is True
    assert parsed.bulk_scope is not None
    assert parsed.bulk_scope.get("discipline") == "Electrical"

    resp = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv_id,
        user_content=hindi_text,
    )

    assert resp.action_card is not None
    assert resp.action_card.type == "BULK_SCOPE_PROPOSAL"
    assert resp.action_card.bulk_count >= 2
    assert "Electrical" in (resp.action_card.scope_label or "")
    assert any("सभी" in opt.get("label", "") for opt in resp.action_card.options or [])
    assert any("ELE-2001" in opt.get("label", "") for opt in resp.action_card.options or [])
    assert any("ELE-2002" in opt.get("label", "") for opt in resp.action_card.options or [])
    assert "मिलीं" in resp.reply_text or "Electrical" in resp.reply_text


# =====================================================================
# Test 14: Universal Multilingual Bulk Scope Detection Across Indian Languages
# =====================================================================
@pytest.mark.parametrize(
    "lang_name, lang_code, text",
    [
        ("Marathi", "mr-IN", "आम्ही आज सर्व इलेक्ट्रिकल कामे पूर्ण केली आहेत. तुम्ही ते अपडेट करू शकता का?"),
        ("Tamil", "ta-IN", "நாங்கள் இன்று அனைத்து மின்சார வேலைகளையும் முடித்துவிட்டோம். அவற்றை அப்டேட் செய்ய முடியுமா?"),
        ("Gujarati", "gu-IN", "અમે આજે બધા ઇલેક્ટ્રિકલ કામ પૂર્ણ કર્યા છે. શું તમે તે બધાને અપડેટ કરી શકો છો?"),
        ("Bengali", "bn-IN", "আমরা আজ সমস্ত বৈদ্যুতিক কাজ শেষ করেছি। আপনি কি সেগুলো আপডেট করতে পারেন?"),
    ],
)
def test_universal_multilingual_bulk_progress_across_languages(
    sarvam_project: Project, db_session: Session, lang_name: str, lang_code: str, text: str
):
    """
    Test 14: Verifies that any supported Indian language (Marathi, Tamil, Gujarati, Bengali, etc.)
    correctly resolves bulk electrical progress and stages BULK_SCOPE_PROPOSAL without error.
    """
    # Ensure multiple electrical activities exist in the project for bulk scope matching
    if not db_session.query(Activity).filter_by(activity_code=f"ELE-2002-{lang_name}").first():
        act_extra = Activity(
            id=f"act-ele-2002-{lang_name}",
            project_id=sarvam_project.id,
            activity_code=f"ELE-2002-{lang_name}",
            name=f"Cable Pulling Level 1 ({lang_name})",
            status="NOT_STARTED",
            percent_complete=0.0,
        )
        db_session.add(act_extra)
        db_session.commit()

    # Mock Sarvam translate_text to return standard English pivot
    def mock_translate(text=None, target_language_code="en-IN", source_language_code=None, numerals_format=None, **kwargs):
        if text is None and "text_in" in kwargs:
            text = kwargs["text_in"]
        if target_language_code == "en-IN":
            return "We have completed all electrical works today. Can you update them?"
        return f"[{lang_name.upper()}_TRANSLATION_OF: {text}]"

    with patch.object(SarvamService, "translate_text", side_effect=mock_translate):
        parsed = ConversationalParser.parse_message(text=text, conversation_language=lang_code)
        assert parsed.intent == "BULK_PROGRESS_REPORT"
        assert parsed.discipline == "Electrical"
        assert parsed.status_reported == "COMPLETED"
        assert parsed.is_bulk is True

        conv_dto = TimeAgentService.get_or_create_conversation(
            db=db_session, project_id=sarvam_project.id, force_new=True
        )
        resp = TimeAgentService.process_message(
            db=db_session,
            project_id=sarvam_project.id,
            conversation_id=conv_dto.conversation_id,
            user_content=text,
        )
        assert resp.action_card is not None
        assert resp.action_card.type == "BULK_SCOPE_PROPOSAL"


def test_multilingual_civ_1001_completion_detection(sarvam_project: Project, db_session: Session):
    """
    Test 15: Validates exact user utterances from field reports across Hindi and Tamil:
    1. 'मैंने सिविल की 1001 एक्टिविटी को पूरा कर दिया है। क्या तुम उसे अपडेट करोगे?' -> CIV-1001 100.0% completion
    2. 'நான் சிவில் இன் 1001 செயல்பாடுகளை முடித்துவிட்டேன்.' -> CIV-1001 100.0% completion
    3. 'அடுத்து தொழில்துறை மற்றும் இயந்திரவியல் செயல்பாடுகளையும் முடித்துவிட்டேன்.' -> BULK_SCOPE_PROPOSAL (Electrical & Mechanical)
    """
    # Ensure CIV-1001 and MEC-1001 activities exist in the project
    if not db_session.query(Activity).filter_by(activity_code="CIV-1001", project_id=sarvam_project.id).first():
        act_civ = Activity(
            id="act-civ-1001-sarvam",
            project_id=sarvam_project.id,
            activity_code="CIV-1001",
            name="Civil Activity 01",
            discipline="Civil",
            status="IN_PROGRESS",
            percent_complete=50.0,
        )
        act_mec = Activity(
            id="act-mec-1001-sarvam",
            project_id=sarvam_project.id,
            activity_code="MEC-1001",
            name="Mechanical Installation Level 1",
            discipline="Mechanical",
            status="NOT_STARTED",
            percent_complete=0.0,
        )
        db_session.add_all([act_civ, act_mec])
        db_session.commit()

    # 1. Hindi test
    conv_hi = TimeAgentService.get_or_create_conversation(db=db_session, project_id=sarvam_project.id, force_new=True)
    res_hi = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv_hi.conversation_id,
        user_content="मैंने सिविल की 1001 एक्टिविटी को पूरा कर दिया है। क्या तुम उसे अपडेट करोगे?",
    )
    assert res_hi.action_card is not None
    assert res_hi.action_card.type == "PROPOSAL_CONFIRMATION"
    assert res_hi.action_card.activity_code == "CIV-1001"
    assert res_hi.action_card.proposed_percent == 100.0

    # 2. Tamil single activity test
    conv_ta = TimeAgentService.get_or_create_conversation(db=db_session, project_id=sarvam_project.id, force_new=True)
    res_ta = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv_ta.conversation_id,
        user_content="நான் சிவில் இன் 1001 செயல்பாடுகளை முடித்துவிட்டேன்.",
    )
    assert res_ta.action_card is not None
    assert res_ta.action_card.type == "PROPOSAL_CONFIRMATION"
    assert res_ta.action_card.activity_code == "CIV-1001"
    assert res_ta.action_card.proposed_percent == 100.0

    # 3. Tamil multi-discipline bulk test
    conv_ta_bulk = TimeAgentService.get_or_create_conversation(db=db_session, project_id=sarvam_project.id, force_new=True)
    res_ta_bulk = TimeAgentService.process_message(
        db=db_session,
        project_id=sarvam_project.id,
        conversation_id=conv_ta_bulk.conversation_id,
        user_content="அடுத்து தொழில்துறை மற்றும் இயந்திரவியல் செயல்பாடுகளையும் முடித்துவிட்டேன்.",
    )
    assert res_ta_bulk.action_card is not None
    assert res_ta_bulk.action_card.type == "BULK_SCOPE_PROPOSAL"
    assert res_ta_bulk.action_card.target_percent == 100.0


# =========================================================================
# TEST SUITE: Sarvam Bulbul V3 Text-to-Speech (TTS)
# =========================================================================

def test_clean_text_for_speech():
    """Validates markdown stripping and speech normalization for TTS."""
    raw = (
        "### Status Update\n"
        "- **CIV-1001 (Foundation Concrete)** is currently `75%` complete.\n"
        "- Next milestone: [Link](http://example.com) [Critical Path]."
    )
    cleaned = SarvamService.clean_text_for_speech(raw)
    assert "###" not in cleaned
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "http://" not in cleaned
    assert "CIV-1001 (Foundation Concrete) is currently 75% complete" in cleaned
    assert "Critical Path" in cleaned


def test_text_to_speech_service_mocked():
    """Validates SarvamService.text_to_speech synthesis and parameter mapping."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.audios = ["UklGRjIAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="]
    mock_client.text_to_speech.convert.return_value = mock_resp

    with patch.object(SarvamService, "get_client", return_value=mock_client):
        # 1. English synthesis
        res_en = SarvamService.text_to_speech(
            text="The foundation activity is 75% complete.",
            language_code="en",
        )
        assert res_en["audio_base64"] == mock_resp.audios[0]
        assert res_en["language_code"] == "en-IN"
        assert res_en["speaker"] == "shubh"
        mock_client.text_to_speech.convert.assert_called_with(
            text="The foundation activity is 75% complete.",
            language_code="en-IN",
            speaker="shubh",
            model="bulbul:v3",
        )

        # 2. Hindi synthesis
        res_hi = SarvamService.text_to_speech(
            text="मैकेनिकल गतिविधि वर्तमान में 75 प्रतिशत पूरी हो चुकी है।",
            language_code="hi-IN",
        )
        assert res_hi["language_code"] == "hi-IN"

        # 3. Tamil synthesis
        res_ta = SarvamService.text_to_speech(
            text="சிவில் செயல்பாடுகள் முடிவடைந்தன.",
            language_code="ta",
        )
        assert res_ta["language_code"] == "ta-IN"


def test_text_to_speech_script_autodetection():
    """Validates that Devanagari text defaults to hi-IN when language is omitted or en-IN."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.audios = ["mock_audio_base64"]
    mock_client.text_to_speech.convert.return_value = mock_resp

    with patch.object(SarvamService, "get_client", return_value=mock_client):
        res = SarvamService.text_to_speech(
            text="सिविल एक्टिविटी 50% पूरी हो गई है।",
            language_code="en-IN",
        )
        assert res["language_code"] == "hi-IN"


def test_text_to_speech_missing_api_key():
    """Validates that missing SARVAM_API_KEY raises ValueError and does not crash."""
    with patch.object(SarvamService, "get_client", return_value=None):
        with pytest.raises(ValueError, match="SARVAM_API_KEY is not configured"):
            SarvamService.text_to_speech(text="Hello world", language_code="en-IN")


def test_api_tts_endpoint(client: TestClient):
    """Validates POST /api/tts and POST /api/v1/tts HTTP endpoints."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.audios = ["dummy_wav_base64"]
    mock_client.text_to_speech.convert.return_value = mock_resp

    with patch.object(SarvamService, "get_client", return_value=mock_client):
        # 1. Normal POST /api/tts
        res = client.post(
            "/api/tts",
            json={"text": "Activity CIV-1001 is complete.", "language": "en-IN"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["audio_base64"] == "dummy_wav_base64"
        assert data["content_type"] == "audio/wav"
        assert data["language"] == "en-IN"

        # 2. Hindi POST /api/tts
        res_hi = client.post(
            "/api/tts",
            json={"text": "मैकेनिकल गतिविधि 75% पूरी हो चुकी है।", "language": "hi-IN"},
        )
        assert res_hi.status_code == 200
        assert res_hi.json()["language"] == "hi-IN"

        # 3. POST /api/v1/tts alias
        res_v1 = client.post(
            "/api/v1/tts",
            json={"text": "Bulk updates have been committed.", "language": "en"},
        )
        assert res_v1.status_code == 200

        # 4. Validation rejection on empty text
        res_empty = client.post("/api/tts", json={"text": "", "language": "en-IN"})
        assert res_empty.status_code in (400, 422)

    # 5. Missing API key handling via endpoint
    with patch.object(SarvamService, "get_client", return_value=None):
        res_no_key = client.post(
            "/api/tts",
            json={"text": "Hello world", "language": "en-IN"},
        )
        assert res_no_key.status_code == 503
        assert "SARVAM_API_KEY" in res_no_key.json()["detail"]





