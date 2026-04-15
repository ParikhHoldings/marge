def test_members_crud_notes_and_draft(client):
    create = client.post(
        "/members/",
        json={
            "first_name": "Martha",
            "last_name": "Lane",
            "email": "martha@example.com",
        },
    )
    assert create.status_code == 201
    member_id = create.json()["id"]

    listed = client.get("/members/?q=Martha")
    assert listed.status_code == 200
    assert any(item["id"] == member_id for item in listed.json())

    note = client.post(f"/members/{member_id}/notes", json={"note_text": "Family stress this week.", "context_tag": "family"})
    assert note.status_code == 201

    detail = client.get(f"/members/{member_id}")
    assert detail.status_code == 200
    assert len(detail.json()["notes"]) == 1

    draft = client.get(f"/members/{member_id}/draft/care?situation=grief")
    assert draft.status_code == 200
    assert draft.json()["member_id"] == member_id

    patch = client.patch(f"/members/{member_id}", json={"phone": "555-111-0000"})
    assert patch.status_code == 200
    assert patch.json()["phone"] == "555-111-0000"

    delete = client.delete(f"/members/{member_id}")
    assert delete.status_code == 204

    missing = client.get(f"/members/{member_id}")
    assert missing.status_code == 404
