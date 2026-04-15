
def test_chat_logs_structured_actions_and_writes_records(client, db_session, seed_dataset):
    response = client.post(
        "/chat/",
        json={
            "pastor_name": "Pastor Lee",
            "message": "Please log that Henry Hospital visited in the hospital and pray for his recovery.",
        },
    )

    assert response.status_code == 200
    data = response.json()
    action_types = {a["type"] for a in data["actions"]}

    assert "member_note" in action_types
    assert "care_case" in action_types
    assert "prayer_request" in action_types
    assert data["drafts"]
    assert "Got it" in data["reply"]


def test_chat_prepares_visitor_sequence_for_known_visitor(client, seed_dataset):
    response = client.post(
        "/chat/",
        json={
            "pastor_name": "Pastor Lee",
            "message": "Prepare the visitor follow-up sequence for Victor Visitor.",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert any(a["type"] == "visitor_sequence" for a in data["actions"])
    assert len(data["drafts"]) >= 3
