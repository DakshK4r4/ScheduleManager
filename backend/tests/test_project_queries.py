from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityRelationship, Project, WBSNode


@pytest.fixture
def copilot_project(db_session: Session):
    """
    Sets up a full-featured project with Civil, Mechanical, and Electrical activities,
    WBS hierarchy, CPM relationships, and varying completion statuses.
    """
    proj = Project(
        id="proj-copilot-test",
        project_code="PROJECT_OMEGA",
        name="Omega Industrial Complex",
        planned_start=datetime(2024, 10, 1, 8, 0),
        planned_finish=datetime(2025, 6, 30, 17, 0),
        data_date=datetime(2024, 10, 10, 0, 0),
    )
    db_session.add(proj)

    # WBS Nodes
    wbs_civ = WBSNode(id="wbs-civ", project_id=proj.id, code="WBS-1.1", name="Civil & Foundations")
    wbs_mec = WBSNode(id="wbs-mec", project_id=proj.id, code="WBS-1.2", name="Mechanical")
    wbs_ele = WBSNode(id="wbs-ele", project_id=proj.id, code="WBS-1.3", name="Electrical & Instrumentation")
    db_session.add_all([wbs_civ, wbs_mec, wbs_ele])

    # Civil Activities
    act_exc = Activity(
        id="act-exc-101",
        project_id=proj.id,
        wbs_id=wbs_civ.id,
        activity_code="CIV-101",
        name="Excavation and Site Preparation",
        discipline="Civil",
        status="COMPLETED",
        planned_start=datetime(2024, 10, 1, 8, 0),
        planned_finish=datetime(2024, 10, 5, 17, 0),
        actual_start=datetime(2024, 10, 1, 8, 0),
        actual_finish=datetime(2024, 10, 5, 17, 0),
        original_duration=5.0,
        percent_complete=100.0,
    )
    act_fnd = Activity(
        id="act-fnd-102",
        project_id=proj.id,
        wbs_id=wbs_civ.id,
        activity_code="CIV-102",
        name="Foundation Concrete Pouring",
        discipline="Civil",
        status="IN_PROGRESS",
        planned_start=datetime(2024, 10, 6, 8, 0),
        planned_finish=datetime(2024, 10, 15, 17, 0),
        actual_start=datetime(2024, 10, 6, 8, 0),
        original_duration=10.0,
        percent_complete=50.0,
    )

    # Mechanical Activities
    act_mec1 = Activity(
        id="act-mec-201",
        project_id=proj.id,
        wbs_id=wbs_mec.id,
        activity_code="MEC-201",
        name="HVAC Installation",
        discipline="Mechanical",
        status="COMPLETED",
        planned_start=datetime(2024, 10, 10, 8, 0),
        planned_finish=datetime(2024, 10, 20, 17, 0),
        actual_start=datetime(2024, 10, 10, 8, 0),
        actual_finish=datetime(2024, 10, 20, 17, 0),
        original_duration=10.0,
        percent_complete=100.0,
    )
    act_mec2 = Activity(
        id="act-mec-202",
        project_id=proj.id,
        wbs_id=wbs_mec.id,
        activity_code="MEC-202",
        name="Pipe Installation and Routing",
        discipline="Mechanical",
        status="IN_PROGRESS",
        planned_start=datetime(2024, 10, 12, 8, 0),
        planned_finish=datetime(2024, 10, 30, 17, 0),
        actual_start=datetime(2024, 10, 12, 8, 0),
        original_duration=18.0,
        percent_complete=40.0,
    )
    act_mec3 = Activity(
        id="act-mec-203",
        project_id=proj.id,
        wbs_id=wbs_mec.id,
        activity_code="MEC-203",
        name="Mechanical Equipment Setup",
        discipline="Mechanical",
        status="NOT_STARTED",
        planned_start=datetime(2024, 10, 16, 8, 0),
        planned_finish=datetime(2024, 11, 20, 17, 0),
        original_duration=35.0,  # Longest duration in mechanical
        percent_complete=0.0,
    )
    act_mec4 = Activity(
        id="act-mec-204",
        project_id=proj.id,
        wbs_id=wbs_mec.id,
        activity_code="MEC-204",
        name="Pump Installation",
        discipline="Mechanical",
        status="NOT_STARTED",
        planned_start=datetime(2024, 10, 8, 8, 0),
        planned_finish=datetime(2024, 10, 9, 17, 0),  # Past data date -> overdue / delayed
        original_duration=2.0,
        percent_complete=0.0,
    )

    # Electrical Activities
    act_ele1 = Activity(
        id="act-ele-301",
        project_id=proj.id,
        wbs_id=wbs_ele.id,
        activity_code="ELE-301",
        name="Cable Tray Installation",
        discipline="Electrical",
        status="IN_PROGRESS",
        planned_start=datetime(2024, 10, 10, 8, 0),
        planned_finish=datetime(2024, 10, 11, 17, 0),  # Due tomorrow relative to data_date Oct 10
        original_duration=2.0,
        percent_complete=30.0,
    )
    act_ele2 = Activity(
        id="act-ele-302",
        project_id=proj.id,
        wbs_id=wbs_ele.id,
        activity_code="ELE-302",
        name="Main Transformer Wiring",
        discipline="Electrical",
        status="NOT_STARTED",
        planned_start=datetime(2024, 10, 10, 8, 0),
        planned_finish=datetime(2024, 10, 10, 17, 0),  # Due today
        original_duration=1.0,
        percent_complete=0.0,
    )

    db_session.add_all([act_exc, act_fnd, act_mec1, act_mec2, act_mec3, act_mec4, act_ele1, act_ele2])

    # Predecessor -> Successor Relationships
    # Excavation -> Foundation -> Mechanical Setup
    rel1 = ActivityRelationship(
        id="rel-1",
        project_id=proj.id,
        predecessor_id=act_exc.id,
        successor_id=act_fnd.id,
        relationship_type="FS",
        lag=0.0,
    )
    rel2 = ActivityRelationship(
        id="rel-2",
        project_id=proj.id,
        predecessor_id=act_fnd.id,
        successor_id=act_mec3.id,
        relationship_type="FS",
        lag=0.0,
    )
    db_session.add_all([rel1, rel2])
    db_session.commit()
    return proj


def _ask_agent(client: TestClient, project_id: str, conv_id: str, query: str) -> str:
    res = client.post(
        f"/api/v1/projects/{project_id}/agent/conversations/{conv_id}/messages",
        json={"content": query},
    )
    assert res.status_code == 200
    return res.json()["reply_text"]


def test_activity_discipline_and_search_queries(client: TestClient, copilot_project: Project):
    """
    Requirement:
    "Give me all activities in mechanical."
    "Show mechanical activities."
    "List all electrical activities."
    "What activities are under civil?"
    "How many mechanical activities are there?"
    "Show completed mechanical activities."
    "Show incomplete mechanical activities."
    "Show delayed mechanical activities."
    """
    res_conv = client.post(
        f"/api/v1/projects/{copilot_project.id}/agent/conversations",
        json={"force_new": True, "title": "Activity Queries"},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. "Give me all the activities in mechanical"
    reply1 = _ask_agent(client, copilot_project.id, conv_id, "Give me all the activities in mechanical")
    assert "MEC-201" in reply1
    assert "MEC-202" in reply1
    assert "HVAC Installation" in reply1
    assert "Pump Installation" in reply1
    assert "4" in reply1  # 4 mechanical activities

    # 2. "Show mechanical activities."
    reply2 = _ask_agent(client, copilot_project.id, conv_id, "Show mechanical activities.")
    assert "MEC-201" in reply2
    assert "MEC-203" in reply2

    # 3. "List all electrical activities."
    reply3 = _ask_agent(client, copilot_project.id, conv_id, "List all electrical activities.")
    assert "ELE-301" in reply3
    assert "ELE-302" in reply3
    assert "Cable Tray Installation" in reply3

    # 4. "What activities are under civil?"
    reply4 = _ask_agent(client, copilot_project.id, conv_id, "What activities are under civil?")
    assert "CIV-101" in reply4
    assert "CIV-102" in reply4
    assert "Excavation and Site Preparation" in reply4

    # 5. "How many mechanical activities are there?"
    reply5 = _ask_agent(client, copilot_project.id, conv_id, "How many mechanical activities are there?")
    assert "4" in reply5 or "Total Activities: 4" in reply5

    # 6. "Show completed mechanical activities."
    reply6 = _ask_agent(client, copilot_project.id, conv_id, "Show completed mechanical activities.")
    assert "MEC-201" in reply6
    assert "MEC-202" not in reply6  # In progress, not completed

    # 7. "Show incomplete mechanical activities."
    reply7 = _ask_agent(client, copilot_project.id, conv_id, "Show incomplete mechanical activities.")
    assert "MEC-202" in reply7
    assert "MEC-203" in reply7
    assert "MEC-201" not in reply7  # Completed, not incomplete

    # 8. "Show delayed mechanical activities."
    reply8 = _ask_agent(client, copilot_project.id, conv_id, "Show delayed mechanical activities.")
    assert "MEC-204" in reply8  # Past data date Oct 10


def test_status_queries(client: TestClient, copilot_project: Project):
    """
    Requirement:
    "What is completed?"
    "What is pending?"
    "What is currently in progress?"
    "Which activities are overdue?"
    """
    res_conv = client.post(
        f"/api/v1/projects/{copilot_project.id}/agent/conversations",
        json={"force_new": True, "title": "Status Queries"},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. "What is completed?"
    reply_comp = _ask_agent(client, copilot_project.id, conv_id, "What is completed?")
    assert "CIV-101" in reply_comp
    assert "MEC-201" in reply_comp

    # 2. "What is pending?"
    reply_pend = _ask_agent(client, copilot_project.id, conv_id, "What is pending?")
    assert "MEC-203" in reply_pend or "MEC-204" in reply_pend

    # 3. "What is currently in progress?"
    reply_prog = _ask_agent(client, copilot_project.id, conv_id, "What is currently in progress?")
    assert "CIV-102" in reply_prog or "MEC-202" in reply_prog

    # 4. "Which activities are overdue?"
    reply_overdue = _ask_agent(client, copilot_project.id, conv_id, "Which activities are overdue?")
    assert "MEC-204" in reply_overdue


def test_dependency_and_critical_path_queries(client: TestClient, copilot_project: Project):
    """
    Requirement:
    "What comes after Foundation?"
    "What is the predecessor of Excavation?"
    "Which activities depend on Foundation?"
    "What is the critical path?"
    "Which activities are critical?"
    "Which activities have zero float?"
    "When will the project finish?"
    """
    res_conv = client.post(
        f"/api/v1/projects/{copilot_project.id}/agent/conversations",
        json={"force_new": True, "title": "Dependency Queries"},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. "What comes after Foundation?"
    reply_succ = _ask_agent(client, copilot_project.id, conv_id, "What comes after Foundation?")
    assert "MEC-203" in reply_succ or "Mechanical Equipment Setup" in reply_succ

    # 2. "Which activities depend on Foundation?"
    reply_dep = _ask_agent(client, copilot_project.id, conv_id, "Which activities depend on Foundation?")
    assert "MEC-203" in reply_dep or "Mechanical Equipment Setup" in reply_dep

    # 3. "What is the predecessor of Activity CIV-102?"
    reply_pred = _ask_agent(client, copilot_project.id, conv_id, "What is the predecessor of Activity CIV-102?")
    assert "CIV-101" in reply_pred or "Excavation" in reply_pred

    # 4. "What is the critical path?"
    reply_cp = _ask_agent(client, copilot_project.id, conv_id, "What is the critical path?")
    assert "Critical Path" in reply_cp
    assert "PROJECT_OMEGA" in reply_cp

    # 5. "Which activities are critical?"
    reply_crit = _ask_agent(client, copilot_project.id, conv_id, "Which activities are critical?")
    assert "Critical" in reply_crit or "critical" in reply_crit

    # 6. "Which activities have zero float?"
    reply_float = _ask_agent(client, copilot_project.id, conv_id, "Which activities have zero float?")
    assert "float" in reply_float.lower() or "0" in reply_float

    # 7. "When will the project finish?"
    reply_fin = _ask_agent(client, copilot_project.id, conv_id, "When will the project finish?")
    assert "Planned Finish" in reply_fin or "Forecast Finish" in reply_fin


def test_conversational_follow_ups(client: TestClient, copilot_project: Project):
    """
    Requirement 8:
    User: "Show me all mechanical activities."
    Agent: "There are 4 mechanical activities..."
    User: "Which ones are delayed?"
    Agent: Filters to mechanical activities that are delayed.
    User: "How many are completed?"
    Agent: Aggregates completion across previously discussed mechanical scope.
    User: "Which one has the longest duration?"
    Agent: Returns longest mechanical activity (MEC-203, 35 days).
    """
    res_conv = client.post(
        f"/api/v1/projects/{copilot_project.id}/agent/conversations",
        json={"force_new": True, "title": "Follow-Up Flow"},
    )
    conv_id = res_conv.json()["conversation_id"]

    # Turn 1
    t1 = _ask_agent(client, copilot_project.id, conv_id, "Show me all mechanical activities.")
    assert "MEC-201" in t1
    assert "MEC-202" in t1
    assert "MEC-203" in t1
    assert "MEC-204" in t1

    # Turn 2: Follow-up on delayed
    t2 = _ask_agent(client, copilot_project.id, conv_id, "Which ones are delayed?")
    assert "MEC-204" in t2
    assert "MEC-201" not in t2  # MEC-201 is completed, not delayed

    # Turn 3: Follow-up on count completed
    t3 = _ask_agent(client, copilot_project.id, conv_id, "How many are completed?")
    assert "1 completed" in t3 or "completed: 1" in t3.lower() or "1" in t3

    # Turn 4: Follow-up on longest duration
    t4 = _ask_agent(client, copilot_project.id, conv_id, "Which one has the longest duration?")
    assert "MEC-203" in t4
    assert "35" in t4


def test_modification_vs_query_distinction(client: TestClient, copilot_project: Project):
    """
    Requirement 14:
    Must distinguish between:
    Query: "Show mechanical activities."
    Action: "Update Foundation to 80%."
    Simulation: "What happens if Foundation is delayed by 5 days?"
    """
    res_conv = client.post(
        f"/api/v1/projects/{copilot_project.id}/agent/conversations",
        json={"force_new": True, "title": "Action vs Query"},
    )
    conv_id = res_conv.json()["conversation_id"]

    # 1. Action: update request
    res_act = client.post(
        f"/api/v1/projects/{copilot_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "Update Foundation Concrete Pouring to 80%."},
    )
    assert res_act.status_code == 200
    card = res_act.json().get("action_card")
    assert card is not None
    assert card["type"] == "PROPOSAL_CONFIRMATION"

    # Cancel action
    client.post(
        f"/api/v1/projects/{copilot_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "cancel"},
    )

    # 2. Simulation
    res_sim = client.post(
        f"/api/v1/projects/{copilot_project.id}/agent/conversations/{conv_id}/messages",
        json={"content": "What happens if Foundation Concrete Pouring is delayed by 5 days?"},
    )
    assert res_sim.status_code == 200
    sim_reply = res_sim.json()["reply_text"]
    assert "Simulation" in sim_reply
    assert "Impact" in sim_reply or "Finish" in sim_reply
