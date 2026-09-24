import pytest
from app.domain.models import Activity, Project, WBSNode

def seed_project(db_session):
    proj = Project(
        id="proj-123",
        project_code="TEST-PROJ",
        name="Test Project",
    )
    wbs = WBSNode(
        id="wbs-123",
        project_id="proj-123",
        code="WBS-01",
        name="Engineering",
    )
    act1 = Activity(
        id="act-1",
        project_id="proj-123",
        wbs_id="wbs-123",
        activity_code="ACT-01",
        name="Foundation",
        status="COMPLETED",
        percent_complete=100.0,
        original_duration=10.0,
    )
    act2 = Activity(
        id="act-2",
        project_id="proj-123",
        wbs_id="wbs-123",
        activity_code="ACT-02",
        name="Framing",
        status="NOT_STARTED",
        percent_complete=0.0,
        original_duration=15.0,
    )
    db_session.add_all([proj, wbs, act1, act2])
    db_session.commit()
    return proj, wbs, act1, act2

def test_list_activities_with_filtering(client, db_session):
    seed_project(db_session)

    # All activities
    res = client.get("/projects/proj-123/activities")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Filter by status
    res = client.get("/projects/proj-123/activities?status=COMPLETED")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["activity_code"] == "ACT-01"

    # Filter by activity_code search
    res = client.get("/projects/proj-123/activities?activity_code=ACT-02")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Framing"

def test_update_activity_patch(client, db_session):
    seed_project(db_session)

    # Patch percent complete and status
    patch_payload = {
        "status": "IN_PROGRESS",
        "percent_complete": 55.5,
        "name": "Updated Foundation Name",
    }
    res = client.patch("/activities/act-1", json=patch_payload)
    assert res.status_code == 200
    updated = res.json()
    assert updated["status"] == "IN_PROGRESS"
    assert updated["percent_complete"] == 55.5
    assert updated["name"] == "Updated Foundation Name"

    # Verify invalid percent complete rejection
    invalid_patch = {"percent_complete": 150.0}
    res_err = client.patch("/activities/act-1", json=invalid_patch)
    assert res_err.status_code == 422


def test_create_and_get_activity_with_metadata(client, db_session):
    seed_project(db_session)

    create_payload = {
        "activity_code": "CIV-9001",
        "name": "Cooling Tower Foundation",
        "wbs_id": "wbs-123",
        "activity_type": "TT_Task",
        "status": "NOT_STARTED",
        "planned_duration": 30.0,
        "location_code": "LOC-CT-01",
        "discipline": "Civil",
        "contractor_name": "Larsen & Toubro",
        "planned_quantity": 450.5,
        "quantity_unit": "m3",
        "is_critical": True,
        "total_float": 0.0,
        "free_float": 0.0,
    }
    res_create = client.post("/projects/proj-123/activities", json=create_payload)
    assert res_create.status_code == 201
    act_data = res_create.json()
    assert act_data["activity_code"] == "CIV-9001"
    assert act_data["location_code"] == "LOC-CT-01"
    assert act_data["discipline"] == "Civil"
    assert act_data["contractor_name"] == "Larsen & Toubro"
    assert act_data["planned_quantity"] == 450.5
    assert act_data["quantity_unit"] == "m3"
    assert act_data["is_critical"] is True

    # Verify retrieval via GET /activities/{id}
    res_get = client.get(f"/activities/{act_data['id']}")
    assert res_get.status_code == 200
    retrieved = res_get.json()
    assert retrieved["location_code"] == "LOC-CT-01"
    assert retrieved["discipline"] == "Civil"
    assert retrieved["contractor_name"] == "Larsen & Toubro"
    assert retrieved["planned_quantity"] == 450.5

    # Verify in list activities
    res_list = client.get("/projects/proj-123/activities?activity_code=CIV-9001")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert list_data["total"] == 1
    assert list_data["items"][0]["location_code"] == "LOC-CT-01"
    assert list_data["items"][0]["discipline"] == "Civil"

