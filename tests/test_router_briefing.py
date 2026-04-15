def test_briefing_router_returns_plaintext_and_sections(client, seed_dataset):
    response = client.get("/briefing/today")
    assert response.status_code == 200

    data = response.json()
    assert data["pastor_name"]
    assert data["church_name"]
    assert data["plain_text"]

    assert any(v["id"] == seed_dataset["visitor_followup_id"] for v in data["visitors_needing_followup"])
    assert any(c["id"] == seed_dataset["care_case_id"] for c in data["active_care_cases"])
    assert any(p["id"] == seed_dataset["prayer_id"] for p in data["unanswered_prayers"])
