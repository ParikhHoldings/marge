from datetime import date


def test_morning_briefing_to_followup_completion_workflow(client, seed_dataset):
    visitor_id = seed_dataset["visitor_followup_id"]
    care_id = seed_dataset["care_case_id"]

    morning = client.get("/briefing/today")
    assert morning.status_code == 200
    assert any(v["id"] == visitor_id for v in morning.json()["visitors_needing_followup"])
    assert any(c["id"] == care_id for c in morning.json()["active_care_cases"])

    day1_draft = client.get(f"/visitors/{visitor_id}/draft?day=1")
    assert day1_draft.status_code == 200

    mark_visitor = client.patch(f"/visitors/{visitor_id}", json={"follow_up_day1_sent": True, "notes": "Sent welcome text."})
    assert mark_visitor.status_code == 200

    log_contact = client.post(f"/care/{care_id}/contact", json={"contact_date": str(date.today()), "note": "Follow-up completed."})
    assert log_contact.status_code == 200

    resolve = client.post(f"/care/{care_id}/resolve")
    assert resolve.status_code == 200

    updated = client.get("/briefing/today")
    assert updated.status_code == 200
    assert all(v["id"] != visitor_id for v in updated.json()["visitors_needing_followup"])
    assert all(c["id"] != care_id for c in updated.json()["active_care_cases"])


def test_tell_marge_logs_structured_actions_workflow(client):
    create_member = client.post("/members/", json={"first_name": "Martha", "last_name": "Ellis", "email": "martha@example.com"})
    assert create_member.status_code == 201

    message = (
        "Martha Ellis is in the hospital after surgery. "
        "Please log a note and prayer request for Martha Ellis."
    )
    chat = client.post("/chat/", json={"pastor_name": "Pastor Jo", "message": message})
    assert chat.status_code == 200

    data = chat.json()
    action_types = {a["type"] for a in data["actions"]}
    assert {"member_note", "care_case", "prayer_request"}.issubset(action_types)

    care_cases = client.get("/care/?status=active")
    assert care_cases.status_code == 200
    assert any(c["member_name"] == "Martha Ellis" and c["category"] == "hospital" for c in care_cases.json())

    prayers = client.get("/care/prayers/?include_private=true")
    assert prayers.status_code == 200
    assert any(p["member_name"] == "Martha Ellis" for p in prayers.json())

    member_notes = client.get(f"/members/{create_member.json()['id']}/notes")
    assert member_notes.status_code == 200
    assert member_notes.json()
