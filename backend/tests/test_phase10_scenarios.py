from __future__ import annotations

import pytest
from app.domain.models import Project


def seed_test_project(db_session):
    proj = Project(
        id="979928c5-928a-4b3e-980b-7c516e1f074d",
        project_code="BOROUGE4_DEMO",
        name="BOROUGE4_DEMO",
    )
    db_session.add(proj)
    db_session.commit()
    return proj


def test_scenario_1_direct_project_url(client, db_session):
    """TEST 1: Direct project lookup on clean load"""
    seed_test_project(db_session)
    res = client.get("/projects/979928c5-928a-4b3e-980b-7c516e1f074d")
    assert res.status_code == 200
    assert res.json()["id"] == "979928c5-928a-4b3e-980b-7c516e1f074d"
    assert res.json()["project_code"] == "BOROUGE4_DEMO"


def test_scenario_4_dashboard_then_project(client, db_session):
    """TEST 4: Load dashboard list and then direct project"""
    seed_test_project(db_session)
    list_res = client.get("/projects")
    assert list_res.status_code == 200
    items = list_res.json()
    assert any(p["id"] == "979928c5-928a-4b3e-980b-7c516e1f074d" for p in items)

    proj_res = client.get("/projects/979928c5-928a-4b3e-980b-7c516e1f074d")
    assert proj_res.status_code == 200
    assert proj_res.json()["project_code"] == "BOROUGE4_DEMO"


def test_scenario_5_open_project_a_then_b(client, db_session):
    """TEST 5: Switching from project A to project B does not bleed state"""
    seed_test_project(db_session)
    res_a = client.get("/projects/979928c5-928a-4b3e-980b-7c516e1f074d")
    assert res_a.status_code == 200

    # Nonexistent project B
    res_b = client.get("/projects/00000000-0000-0000-0000-000000000000")
    assert res_b.status_code == 404


def test_scenario_6_invalid_project_ids(client, db_session):
    """TEST 6: Invalid/literal project identifiers return 400 Bad Request, NOT 404"""
    for bad_id in ["undefined", "null", "none", "nan", "[object%20Object]", "bad!id#"]:
        res = client.get(f"/projects/{bad_id}")
        assert res.status_code == 400, f"Expected 400 for '{bad_id}', got {res.status_code}"
        detail = res.json().get("detail", "")
        assert "Invalid project ID format" in detail or "Malformed project ID" in detail


def test_scenario_7_valid_uuid_nonexistent_project(client, db_session):
    """TEST 7: Valid UUID for nonexistent project returns TRUE 404"""
    res = client.get("/projects/11111111-2222-3333-4444-555555555555")
    assert res.status_code == 404
    assert "not found" in res.json().get("detail", "").lower()


def test_scenario_8_activities_and_cpm_telemetry(client, db_session):
    """TEST 8: Sub-resource validation on project telemetry endpoints"""
    seed_test_project(db_session)
    act_resp = client.get("/projects/979928c5-928a-4b3e-980b-7c516e1f074d/activities")
    assert act_resp.status_code == 200

    cpm_resp = client.get("/projects/979928c5-928a-4b3e-980b-7c516e1f074d/cpm")
    assert cpm_resp.status_code == 200

    # Invalid ID on telemetry endpoints returns 400 Bad Request
    act_inv = client.get("/projects/undefined/activities")
    assert act_inv.status_code == 400
    cpm_inv = client.get("/projects/null/cpm")
    assert cpm_inv.status_code == 400
