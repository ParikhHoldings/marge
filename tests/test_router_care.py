def test_care_cases_and_prayer_routes(client, seed_dataset):
    member_id = seed_dataset["member_hospital_id"]

    create = client.post("/care/", json={"member_id": member_id, "category": "general", "description": "Needs meal train."})
    assert create.status_code == 201
    care_id = create.json()["id"]

    listed = client.get("/care/?status=active")
    assert listed.status_code == 200
    assert any(case["id"] == care_id for case in listed.json())

    contact = client.post(f"/care/{care_id}/contact", json={"note": "Checked in after appointment."})
    assert contact.status_code == 200
    assert "Checked in" in (contact.json()["description"] or "")

    resolved = client.post(f"/care/{care_id}/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    prayer = client.post(
        "/care/prayers/",
        json={"member_id": member_id, "request_text": "Pray for wisdom in treatment.", "is_private": False},
    )
    assert prayer.status_code == 201
    prayer_id = prayer.json()["id"]

    prayer_list = client.get("/care/prayers/?status=active")
    assert prayer_list.status_code == 200
    assert any(item["id"] == prayer_id for item in prayer_list.json())

    prayer_patch = client.patch(f"/care/prayers/{prayer_id}", json={"status": "answered"})
    assert prayer_patch.status_code == 200
    assert prayer_patch.json()["status"] == "answered"
