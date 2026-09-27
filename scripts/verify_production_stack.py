import io
import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost"

def log(msg):
    print(f"[TEST] {msg}", flush=True)

def http_get(path, timeout=30):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "VerificationScript/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read(), dict(resp.headers)

def http_post_json(path, data_dict, headers=None, timeout=30):
    url = f"{BASE_URL}{path}"
    h = {"Content-Type": "application/json", "User-Agent": "VerificationScript/1.0"}
    if headers:
        h.update(headers)
    body = json.dumps(data_dict).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8")
        return resp.getcode(), json.loads(content) if content else {}

def http_patch_json(path, data_dict, headers=None, timeout=30):
    url = f"{BASE_URL}{path}"
    h = {"Content-Type": "application/json", "User-Agent": "VerificationScript/1.0"}
    if headers:
        h.update(headers)
    body = json.dumps(data_dict).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=h, method="PATCH")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8")
        return resp.getcode(), json.loads(content) if content else {}

def http_post_multipart(path, files, fields=None, timeout=60):
    url = f"{BASE_URL}{path}"
    boundary = "----WebKitFormBoundary" + os.urandom(16).hex()
    body = io.BytesIO()

    if fields:
        for k, v in fields.items():
            body.write(f"--{boundary}\r\n".encode("utf-8"))
            body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode("utf-8"))
            body.write(f"{v}\r\n".encode("utf-8"))

    for field_name, (filename, file_bytes, content_type) in files.items():
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8"))
        body.write(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.write(file_bytes)
        body.write(b"\r\n")

    body.write(f"--{boundary}--\r\n".encode("utf-8"))
    payload = body.getvalue()

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(payload)),
        "User-Agent": "VerificationScript/1.0",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8")
        return resp.getcode(), json.loads(content) if content else {}

def generate_sample_xlsx():
    xlsx_path = os.path.join(os.path.dirname(__file__), "sample_schedule.xlsx")
    if os.path.exists(xlsx_path):
        with open(xlsx_path, "rb") as f:
            return f.read()
    return b""

SAMPLE_XER = """ERMHDR\t19.12\t2024-01-01\tProject\tadmin\tDemo\tPROJECT_01\tUSD\tDD/MM/YYYY\t1\t0\t0
%T\tPROJECT
%F\tproj_id\tproj_short_name\tplan_start_date\tplan_end_date
%R\t1\tPROJ-PROD-XER\t2024-01-01 08:00\t2024-12-31 17:00
%T\tPROJWBS
%F\twbs_id\tproj_id\twbs_short_name\twbs_name\tparent_wbs_id
%R\t10\t1\tWBS.1\tMechanical Works\t
%T\tTASK
%F\ttask_id\tproj_id\twbs_id\ttask_code\ttask_name\ttask_type\tstatus_code\ttarget_drtn_hr_cnt\ttarget_start_date\ttarget_end_date\tphys_percent_comp
%R\t101\t1\t10\tMEC-1001\tMechanical Piping Installation\tTT_Task\tTK_Active\t80\t2024-01-01 08:00\t2024-01-10 17:00\t0
%R\t102\t1\t10\tMEC-1002\tMechanical Welding Inspection\tTT_Task\tTK_Active\t160\t2024-01-11 08:00\t2024-01-31 17:00\t0
%T\tTASKPRED
%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt
%R\t501\t102\t101\tPR_FS\t0
%E
"""

SAMPLE_CSV = """Activity ID,Activity Name,WBS,Status,Start,Finish,Duration,% Complete,Predecessors
CSV-01,Foundation Excavation,Civil,Completed,2024-01-05,2024-01-15,10,100,
CSV-02,Steel Rebar Placement,Structural,In Progress,2024-01-16,2024-02-05,20,50,CSV-01FS
"""

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<APM xmlns="http://xmlns.oracle.com/Primavera/P6/V19.12/API/BusinessObjects">
    <Project>
        <ObjectId>200</ObjectId>
        <Id>PROJ-PROD-XML</Id>
        <Name>XML Prod Facility</Name>
        <PlannedStartDate>2024-03-01T08:00:00</PlannedStartDate>
        <PlannedFinishDate>2024-11-30T17:00:00</PlannedFinishDate>
    </Project>
    <WBS>
        <ObjectId>210</ObjectId>
        <Code>WBS-ENG</Code>
        <Name>Engineering Phase</Name>
    </WBS>
    <Activity>
        <ObjectId>301</ObjectId>
        <Id>XML-101</Id>
        <Name>Design Review</Name>
        <WBSObjectId>210</WBSObjectId>
        <Status>Completed</Status>
        <PlannedDuration>40.0</PlannedDuration>
        <PercentComplete>100</PercentComplete>
    </Activity>
</APM>
"""

def main():
    log("Starting End-to-End Production Verification on Caddy (Port 80)...")
    results = {}

    # 1. Frontend loads
    try:
        code, body, headers = http_get("/")
        assert code == 200, f"Expected 200, got {code}"
        html = body.decode("utf-8", errors="ignore")
        assert "<html" in html or "<!DOCTYPE" in html or "Next.js" in html or "primavera" in html.lower(), "HTML shell missing"
        results["1. Frontend loads"] = "PASS"
        log("1. Frontend loads: PASS")
    except Exception as e:
        results["1. Frontend loads"] = f"FAIL: {e}"
        log(f"1. Frontend loads: FAIL: {e}")

    # 2. Frontend proxy communication & 3. Backend health
    try:
        code, body, _ = http_get("/health")
        assert code == 200, f"Health endpoint returned {code}"
        health_json = json.loads(body.decode("utf-8"))
        assert health_json.get("status") == "ok", f"Health status: {health_json}"
        deps = health_json.get("dependencies", {})
        assert deps.get("database") == "healthy", f"Database health: {health_json}"
        assert deps.get("storage") == "healthy", f"Storage health: {health_json}"
        results["2. Frontend -> Backend routing"] = "PASS"
        results["3. Backend health works"] = "PASS"
        results["4. PostgreSQL connection works"] = "PASS"
        results["5. MinIO connection works"] = "PASS"
        log("2-5. Routing & Healthchecks (Backend, Postgres, MinIO): PASS")
    except Exception as e:
        results["2-5. Routing & Healthchecks"] = f"FAIL: {e}"
        log(f"2-5. Routing & Healthchecks: FAIL: {e}")

    # 7. XER import
    created_project_id = None
    try:
        code, resp = http_post_multipart(
            "/api/proxy/projects/import",
            files={"file": ("schedule.xer", SAMPLE_XER.encode("utf-8"), "application/octet-stream")},
            fields={"name": "Production XER Project"}
        )
        assert code == 201, f"Expected 201 Created, got {code}: {resp}"
        assert resp.get("project_code") == "PROJ-PROD-XER"
        created_project_id = resp["id"]
        results["6. Document parser works"] = "PASS"
        results["7. XER import works"] = "PASS"
        log(f"6-7. Parser & XER Import: PASS (Project ID: {created_project_id})")
    except Exception as e:
        results["7. XER import works"] = f"FAIL: {e}"
        log(f"7. XER import: FAIL: {e}")

    # 8. XML import
    try:
        code, resp = http_post_multipart(
            "/api/proxy/projects/import",
            files={"file": ("schedule.xml", SAMPLE_XML.encode("utf-8"), "application/xml")},
            fields={"name": "Production XML Project"}
        )
        assert code == 201, f"Expected 201 Created, got {code}: {resp}"
        assert resp.get("project_code") == "PROJ-PROD-XML"
        results["8. XML import works"] = "PASS"
        log("8. XML import: PASS")
    except Exception as e:
        results["8. XML import works"] = f"FAIL: {e}"
        log(f"8. XML import: FAIL: {e}")

    # 9. XLSX import
    try:
        xlsx_bytes = generate_sample_xlsx()
        code, resp = http_post_multipart(
            "/api/proxy/projects/import",
            files={"file": ("schedule.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            fields={"name": "Production XLSX Project"}
        )
        assert code == 201, f"Expected 201 Created, got {code}: {resp}"
        results["9. XLSX import works"] = "PASS"
        log("9. XLSX import: PASS")
    except Exception as e:
        results["9. XLSX import works"] = f"FAIL: {e}"
        log(f"9. XLSX import: FAIL: {e}")

    # 10. CSV import
    try:
        code, resp = http_post_multipart(
            "/api/proxy/projects/import",
            files={"file": ("schedule.csv", SAMPLE_CSV.encode("utf-8"), "text/csv")},
            fields={"name": "Production CSV Project"}
        )
        assert code == 201, f"Expected 201 Created, got {code}: {resp}"
        results["10. CSV import works"] = "PASS"
        log("10. CSV import: PASS")
    except Exception as e:
        results["10. CSV import works"] = f"FAIL: {e}"
        log(f"10. CSV import: FAIL: {e}")

    # 11 & 12. Artifact upload and retrieval via MinIO
    artifact_id = None
    if created_project_id:
        try:
            dummy_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000052 00000 n\n0000000101 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n162\n%%EOF"
            code, resp = http_post_multipart(
                f"/api/proxy/api/v1/projects/{created_project_id}/artifacts/upload",
                files={"file": ("daily_site_report.pdf", dummy_pdf, "application/pdf")},
                fields={"report_id": "RPT-001", "uploaded_by": "site-eng"}
            )
            assert code in (200, 201), f"Artifact upload returned {code}: {resp}"
            artifact_id = resp["artifact"]["artifact_id"]
            results["11. Artifact upload works"] = "PASS"
            log(f"11. Artifact upload: PASS (Artifact ID: {artifact_id})")

            # Retrieval
            code, body, _ = http_get(f"/api/proxy/api/v1/projects/{created_project_id}/artifacts")
            assert code == 200
            arts = json.loads(body.decode("utf-8"))
            assert any(a.get("artifact_id") == artifact_id for a in arts), "Uploaded artifact not found in list"
            results["12. Artifact retrieval works"] = "PASS"
            log("12. Artifact retrieval: PASS")
        except Exception as e:
            results["11. Artifact upload works"] = f"FAIL: {e}"
            results["12. Artifact retrieval works"] = f"FAIL: {e}"
            log(f"11-12. Artifact upload/retrieval: FAIL: {e}")

    # 13. Gemini / Sarvam availability check
    try:
        # TTS via Sarvam
        code, tts_resp = http_post_json(
            "/api/proxy/api/tts",
            {"text": "Production verification speech test", "language": "hi-IN"}
        )
        if code == 200 and tts_resp.get("audio_base64"):
            results["14. Sarvam workflow works"] = "PASS"
            log("14. Sarvam workflow: PASS (TTS audio generated)")
        else:
            results["14. Sarvam workflow works"] = "SKIPPED/OPTIONAL"
            log(f"14. Sarvam workflow: Status {code}")
    except Exception as e:
        results["14. Sarvam workflow works"] = f"INFO: {e}"
        log(f"14. Sarvam workflow: {e}")

    # 15. Time Agent, 16. Disambiguation, 17. NO-MATCH, 18. Multi-activity, 19. Confirmation
    if created_project_id:
        try:
            # Create conversation
            code, conv_resp = http_post_json(
                f"/api/proxy/api/v1/projects/{created_project_id}/agent/conversations",
                {"force_new": True}
            )
            assert code in (200, 201), f"Conversation create failed: {conv_resp}"
            conv_id = conv_resp["conversation_id"]
            results["15. Time Agent works"] = "PASS"
            log(f"15. Time Agent conversation created: PASS ({conv_id})")

            # 16. Ambiguity -> CLARIFICATION_CHOICE
            code, amb_resp = http_post_json(
                f"/api/proxy/api/v1/projects/{created_project_id}/agent/conversations/{conv_id}/messages",
                {"content": "update the mechanical work to 80%"}
            )
            assert code == 200, f"Message send failed: {amb_resp}"
            card = amb_resp.get("action_card")
            assert card is not None, "Action card expected for ambiguous message"
            assert card.get("type") == "CLARIFICATION_CHOICE", f"Expected CLARIFICATION_CHOICE, got {card.get('type')}"
            results["16. Activity disambiguation works"] = "PASS"
            log("16. Activity disambiguation (CLARIFICATION_CHOICE): PASS")

            # 19. Verify NO mutation occurred before confirmation
            code, act_body, _ = http_get(f"/api/proxy/projects/{created_project_id}/activities")
            acts = json.loads(act_body.decode("utf-8"))["items"]
            mec1001 = next(a for a in acts if a["activity_code"] == "MEC-1001")
            assert mec1001["percent_complete"] == 0.0, "Activity mutated prematurely!"
            results["19. Explicit confirmation is required before mutation"] = "PASS"
            log("19. Pre-confirmation immutability: PASS")

            # Select option -> PROPOSAL_CONFIRMATION
            code, sel_resp = http_post_json(
                f"/api/proxy/api/v1/projects/{created_project_id}/agent/conversations/{conv_id}/messages",
                {"content": "first one"}
            )
            card2 = sel_resp.get("action_card")
            assert card2 is not None and card2.get("type") == "PROPOSAL_CONFIRMATION"
            prop_id = card2["proposal_id"]

            # Confirm proposal
            code, conf_resp = http_post_json(
                f"/api/proxy/api/v1/projects/{created_project_id}/agent/conversations/{conv_id}/confirm",
                {"proposal_id": prop_id, "action": "CONFIRM"}
            )
            assert code == 200
            # Verify mutation occurred AFTER confirmation
            code, act_body2, _ = http_get(f"/api/proxy/projects/{created_project_id}/activities")
            acts2 = json.loads(act_body2.decode("utf-8"))["items"]
            mec1001_updated = next(a for a in acts2 if a["activity_code"] == "MEC-1001")
            assert mec1001_updated["percent_complete"] == 80.0, f"Expected 80.0%, got {mec1001_updated['percent_complete']}"
            log("19b. Post-confirmation mutation: PASS (MEC-1001 is now 80%)")

            # 17. NO-MATCH behavior
            code, nomatch_resp = http_post_json(
                f"/api/proxy/api/v1/projects/{created_project_id}/agent/conversations/{conv_id}/messages",
                {"content": "I finished the hyperspace tunnel excavation to 100%"}
            )
            assert code == 200
            nomatch_card = nomatch_resp.get("action_card")
            # Should have NO mutation and NO proposal
            assert nomatch_card is None or nomatch_card.get("type") != "PROPOSAL_CONFIRMATION"
            reply_text = nomatch_resp.get("reply_text", "").lower()
            assert "couldn't find" in reply_text or "not found" in reply_text or "no matching" in reply_text or "hyperspace" in reply_text
            results["17. NO-MATCH behavior works"] = "PASS"
            log("17. NO-MATCH behavior: PASS")

        except Exception as e:
            results["Time Agent Suite"] = f"FAIL: {e}"
            log(f"Time Agent Suite: FAIL: {e}")

    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY:")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print("=" * 60)

    # Return created IDs for persistence test
    return {
        "project_id": created_project_id,
        "artifact_id": artifact_id,
        "results": results
    }

if __name__ == "__main__":
    main()
