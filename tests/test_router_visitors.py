from datetime import date, timedelta


def test_visitors_crud_filter_and_drafts(client):
    create = client.post(
        "/visitors/",
        json={
            "first_name": "Jared",
            "last_name": "Guest",
            "email": "jared@example.com",
            "visit_date": str(date.today() - timedelta(days=4)),
            "source": "walk-in",
        },
    )
    assert create.status_code == 201
    visitor_id = create.json()["id"]

    listed = client.get("/visitors/?needs_followup=true")
    assert listed.status_code == 200
    assert any(v["id"] == visitor_id for v in listed.json())

    draft = client.get(f"/visitors/{visitor_id}/draft?day=3")
    assert draft.status_code == 200
    assert draft.json()["visitor_id"] == visitor_id

    patch = client.patch(f"/visitors/{visitor_id}", json={"follow_up_day1_sent": True, "notes": "Texted today."})
    assert patch.status_code == 200
    assert patch.json()["follow_up_day1_sent"] is True

    delete = client.delete(f"/visitors/{visitor_id}")
    assert delete.status_code == 204
    missing = client.get(f"/visitors/{visitor_id}")
    assert missing.status_code == 404
