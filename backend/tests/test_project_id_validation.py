from __future__ import annotations

import pytest
from app.domain.models import Project


def seed_test_project(db_session):
    proj = Project(
        id="valid-project-uuid-1234",
        project_code="TEST-PROJ",
        name="Test Project Valid",
    )
    db_session.add(proj)
    db_session.commit()
    return proj


def test_invalid_project_id_literals_return_400(client, db_session):
    for bad_id in ["undefined", "null", "none", "nan", "[object%20Object]"]:
        res = client.get(f"/projects/{bad_id}")
        assert res.status_code == 400, f"Expected 400 for '{bad_id}', got {res.status_code}"
        assert "Invalid project ID format" in res.json().get("detail", "")


def test_malformed_project_id_returns_400(client, db_session):
    bad_id = "bad$id@special!"
    res = client.get(f"/projects/{bad_id}")
    assert res.status_code == 400
    assert "Malformed project ID" in res.json().get("detail", "")


def test_nonexistent_valid_project_id_returns_404(client, db_session):
    res = client.get("/projects/nonexistent-valid-id-9999")
    assert res.status_code == 404
    assert "not found" in res.json().get("detail", "").lower()


def test_existing_valid_project_id_returns_200(client, db_session):
    seed_test_project(db_session)
    res = client.get("/projects/valid-project-uuid-1234")
    assert res.status_code == 200
    assert res.json()["id"] == "valid-project-uuid-1234"
    assert res.json()["project_code"] == "TEST-PROJ"


def test_child_endpoints_reject_invalid_ids(client, db_session):
    res = client.get("/projects/undefined/activities")
    assert res.status_code == 400

    res = client.get("/projects/undefined/cpm")
    assert res.status_code == 400

    res = client.get("/projects/null/wbs")
    assert res.status_code == 400

    res = client.get("/projects/null/relationships")
    assert res.status_code == 400
