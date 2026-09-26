import json
import urllib.request
import urllib.error
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost:8080"
PROJECT_ID = "d2c363c7-dbe1-4bab-95e2-c96ca02da053"

def make_request(path, method="GET", body=None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8")
        print(f"HTTPError {e.code} on {method} {url}: {err_content}")
        raise

def get_activity_percents():
    data = make_request(f"/projects/{PROJECT_ID}/activities?page_size=100")
    return {a["activity_code"]: a["percent_complete"] for a in data["items"]}

def create_conversation():
    resp = make_request(f"/api/v1/projects/{PROJECT_ID}/agent/conversations", method="POST", body={"force_new": True})
    return resp["conversation_id"]

def send_message(conv_id, content):
    return make_request(
        f"/api/v1/projects/{PROJECT_ID}/agent/conversations/{conv_id}/messages",
        method="POST",
        body={"content": content},
    )

def run_tests():
    print("=" * 70)
    print("STARTING LIVE BLACK-BOX API VERIFICATION AGAINST RUNNING BACKEND")
    print(f"Target: {BASE_URL}, Project: {PROJECT_ID}")
    print("=" * 70)

    initial_percents = get_activity_percents()
    print(f"Initial percents: MEC-1001={initial_percents.get('MEC-1001')}, MEC-1002={initial_percents.get('MEC-1002')}")

    # -------------------------------------------------------------
    # SCENARIO 1: Ambiguous statement produces clarification choice without mutation
    # "update the mechanical work to 80%"
    # -------------------------------------------------------------
    print("\n--- SCENARIO 1: 'update the mechanical work to 80%' ---")
    conv1 = create_conversation()
    res1 = send_message(conv1, "update the mechanical work to 80%")
    print(f"Agent reply: {res1.get('reply_text')}")
    card1 = res1.get("action_card")
    assert card1 is not None, "Expected action_card for ambiguous reference"
    assert card1["type"] == "CLARIFICATION_CHOICE", f"Expected CLARIFICATION_CHOICE, got {card1['type']}"
    assert card1["type"] != "BULK_SCOPE_PROPOSAL", "Must NOT be bulk scope proposal"
    assert len(card1.get("options", [])) >= 2, "Expected options presenting real activities"
    print(f"Action card type: {card1['type']}, Options presented: {len(card1['options'])}")
    for opt in card1["options"][:3]:
        print(f"  - {opt.get('label') or opt.get('value')}")

    # Verify NO database mutation occurred
    current_percents = get_activity_percents()
    assert current_percents["MEC-1001"] == initial_percents["MEC-1001"], "MEC-1001 mutated prematurely!"
    assert current_percents["MEC-1002"] == initial_percents["MEC-1002"], "MEC-1002 mutated prematurely!"
    print("[PASS] Ambiguity produced clarification instead of bulk. No database mutation occurred.")

    # -------------------------------------------------------------
    # SCENARIO 2: Disambiguation turn -> Normal proposal flow -> Confirmation -> Mutation
    # User selects "second one" or "piping" or "Mechanical Activity 02"
    # -------------------------------------------------------------
    print("\n--- SCENARIO 2: Disambiguation answer 'second one' ---")
    res1_step2 = send_message(conv1, "second one")
    print(f"Agent reply: {res1_step2.get('reply_text')}")
    card1_step2 = res1_step2.get("action_card")
    assert card1_step2 is not None, "Expected proposal confirmation card"
    assert card1_step2["type"] == "PROPOSAL_CONFIRMATION", f"Expected PROPOSAL_CONFIRMATION, got {card1_step2['type']}"
    assert card1_step2["proposed_percent"] == 80.0, f"Expected 80.0%, got {card1_step2['proposed_percent']}"
    resolved_code = card1_step2["activity_code"]
    print(f"Resolved activity: {resolved_code}, Proposed %: {card1_step2['proposed_percent']}%")

    # Verify NO mutation BEFORE user confirms
    mid_percents = get_activity_percents()
    assert mid_percents[resolved_code] == initial_percents[resolved_code], f"{resolved_code} mutated before confirmation!"
    print("[PASS] Resolved activity entered PROPOSAL_CONFIRMATION flow. Still no mutation before confirm.")

    # Confirm the proposal
    print("\n--- Confirming the proposal ---")
    prop_id = card1_step2["proposal_id"]
    conf_res = make_request(
        f"/api/v1/projects/{PROJECT_ID}/agent/conversations/{conv1}/confirm",
        method="POST",
        body={"proposal_id": prop_id, "action": "CONFIRM"},
    )
    print(f"Confirm response: {conf_res.get('message')}")
    post_confirm_percents = get_activity_percents()
    assert post_confirm_percents[resolved_code] == 80.0, f"Expected 80.0%, got {post_confirm_percents[resolved_code]}"
    print(f"[PASS] Confirmed update successfully mutated {resolved_code} to {post_confirm_percents[resolved_code]}%!")

    # -------------------------------------------------------------
    # SCENARIO 3: "i have completed mechanical activity 02 98%"
    # -------------------------------------------------------------
    print("\n--- SCENARIO 3: 'i have completed mechanical activity 02 98%' ---")
    conv2 = create_conversation()
    res2 = send_message(conv2, "i have completed mechanical activity 02 98%")
    print(f"Agent reply: {res2.get('reply_text')}")
    card2 = res2.get("action_card")
    assert card2 is not None, "Expected action card"
    assert card2["type"] == "PROPOSAL_CONFIRMATION", f"Expected PROPOSAL_CONFIRMATION, got {card2['type']}"
    assert card2["activity_code"] == "MEC-1002", f"Expected MEC-1002, got {card2['activity_code']}"
    assert card2["proposed_percent"] == 98.0, f"Expected 98.0% (not 100%), got {card2['proposed_percent']}"
    print(f"[PASS] Single activity matched MEC-1002 with explicit 98.0% (precedence preserved over 'completed').")

    # -------------------------------------------------------------
    # SCENARIO 4: "i have done mechanical activity 1 75% and mechanical activity 2 98%"
    # -------------------------------------------------------------
    print("\n--- SCENARIO 4: Multi-activity progress update ---")
    conv3 = create_conversation()
    res3 = send_message(conv3, "i have done mechanical activity 1 75% and mechanical activity 2 98%")
    print(f"Agent reply: {res3.get('reply_text')}")
    card3 = res3.get("action_card")
    assert card3 is not None, "Expected action card"
    assert card3["type"] == "BULK_SCOPE_PROPOSAL", f"Expected multi-activity proposal card, got {card3['type']}"
    assert card3.get("is_multi_activity") is True, "Expected is_multi_activity=True"
    b_acts = card3.get("bulk_activities", [])
    assert len(b_acts) == 2, f"Expected 2 activities, got {len(b_acts)}"
    act_pct_map = {b["activity_code"]: b["proposed_percent"] for b in b_acts}
    assert act_pct_map.get("MEC-1001") == 75.0, f"Expected MEC-1001 to be 75%, got {act_pct_map.get('MEC-1001')}"
    assert act_pct_map.get("MEC-1002") == 98.0, f"Expected MEC-1002 to be 98%, got {act_pct_map.get('MEC-1002')}"
    print(f"[PASS] Multi-activity proposal staged: MEC-1001 at 75%, MEC-1002 at 98%.")

    # Confirm multi-activity proposal
    confirm_bulk_res = make_request(
        f"/api/v1/projects/{PROJECT_ID}/agent/conversations/{conv3}/bulk-confirm",
        method="POST",
        body={"activity_ids": [b["activity_id"] for b in b_acts], "action": "CONFIRM"},
    )
    print(f"Bulk confirm response: {confirm_bulk_res.get('message')}")
    final_percents = get_activity_percents()
    assert final_percents["MEC-1001"] == 75.0, f"Expected MEC-1001 75%, got {final_percents['MEC-1001']}"
    assert final_percents["MEC-1002"] == 98.0, f"Expected MEC-1002 98%, got {final_percents['MEC-1002']}"
    print(f"[PASS] Multi-activity confirmed and applied accurately: MEC-1001=75%, MEC-1002=98%.")

    # -------------------------------------------------------------
    # SCENARIO 5: "update all mechanical activities to 80%" (Explicit bulk intent)
    # -------------------------------------------------------------
    print("\n--- SCENARIO 5: Explicit bulk intent 'update all mechanical activities to 80%' ---")
    conv4 = create_conversation()
    res4 = send_message(conv4, "update all mechanical activities to 80%")
    print(f"Agent reply: {res4.get('reply_text')}")
    card4 = res4.get("action_card")
    assert card4 is not None, "Expected bulk scope action card"
    assert card4["type"] == "BULK_SCOPE_PROPOSAL"
    assert card4.get("is_multi_activity") in (None, False), "Expected standard bulk scope, not multi-activity"
    assert card4.get("target_percent") == 80.0, f"Expected target_percent=80.0, got {card4.get('target_percent')}"
    print(f"[PASS] Explicit bulk intent correctly routed to standard bulk proposal with target_percent=80.0%.")

    # -------------------------------------------------------------
    # SCENARIO 6: Section 32: Non-existent activity "I completed the mechanical welding activity to 80%."
    # -------------------------------------------------------------
    print("\n--- SCENARIO 6: Non-existent activity 'I completed the mechanical welding activity to 80%.' ---")
    conv5 = create_conversation()
    res5 = send_message(conv5, "I completed the mechanical welding activity to 80%.")
    reply5 = res5.get("reply_text", "")
    print(f"Agent reply: {reply5}")
    card5 = res5.get("action_card")
    assert "in this project's schedule" in reply5, "Must reference this project's schedule"
    assert "mechanical welding activity" in reply5 or "welding" in reply5
    assert card5 is None or card5.get("type") != "PROPOSAL_CONFIRMATION", "Must NOT create proposal confirmation"
    print(f"[PASS] Section 32 NO_MATCH statement safely returned with current project schedule reference and suggestions.")

    # -------------------------------------------------------------
    # SCENARIO 7: Hinglish non-existent activity "Mechanical welding activity 80% complete ho gayi hai."
    # -------------------------------------------------------------
    print("\n--- SCENARIO 7: Hinglish non-existent activity ---")
    conv7 = create_conversation()
    res7 = send_message(conv7, "Mechanical welding activity 80% complete ho gayi hai.")
    reply7 = res7.get("reply_text", "")
    print(f"Agent reply: {reply7}")
    card7 = res7.get("action_card")
    assert "'Mechanical welding activity'" in reply7, f"Must quote 'Mechanical welding activity', got: {reply7}"
    assert "activity 80" not in reply7, f"Must NOT quote 'activity 80', got: {reply7}"
    assert card7 is None or card7.get("type") != "PROPOSAL_CONFIRMATION", "Must NOT create proposal confirmation"
    print(f"[PASS] Scenario 7 correctly extracted 'Mechanical welding activity' and returned NO_MATCH without proposal.")

    # -------------------------------------------------------------
    # SCENARIO 8: Hindi non-existent activity "मैंने मैकेनिकल वेल्डिंग एक्टिविटी को 80% पूरा किया।"
    # -------------------------------------------------------------
    print("\n--- SCENARIO 8: Hindi non-existent activity ---")
    conv8 = create_conversation()
    res8 = send_message(conv8, "मैंने मैकेनिकल वेल्डिंग एक्टिविटी को 80% पूरा किया।")
    reply8 = res8.get("reply_text", "")
    print(f"Agent reply: {reply8}")
    card8 = res8.get("action_card")
    assert "'मैकेनिकल वेल्डिंग एक्टिविटी'" in reply8, f"Must quote 'मैकेनिकल वेल्डिंग एक्टिविटी', got: {reply8}"
    assert "प्रोजेक्ट के शेड्यूल में" in reply8, f"Must reference project schedule in Hindi, got: {reply8}"
    assert "ये निर्धारित गतिविधियां संबंधित हो सकती हैं:" in reply8, f"Must present suggestions in Hindi, got: {reply8}"
    assert card8 is None or card8.get("type") != "PROPOSAL_CONFIRMATION", "Must NOT create proposal confirmation"
    print(f"[PASS] Scenario 8 correctly extracted 'मैकेनिकल वेल्डिंग एक्टिविटी' and returned NO_MATCH with suggestions.")

    print("\n" + "=" * 70)
    print("ALL LIVE BLACK-BOX SCENARIOS PASSED WITH AUTHORITATIVE MUTATION CHECKS!")
    print("=" * 70)

if __name__ == "__main__":
    run_tests()
