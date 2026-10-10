"""Happy-path and error-path tests for the bed-flow API."""
from tests.conftest import load_json


def test_seeded_board_shows_ten_patients(client):
    page = client.get("/")
    assert page.status_code == 200
    for i in range(1, 11):
        assert f"PT-{i:04d}" in page.text
    beds = client.get("/api/beds").json()
    assert len(beds) == 10 and all(b["patient_id"] for b in beds)


def test_post_when_full_goes_to_waiting_list(client):
    r = client.post("/api/patients", json=load_json("incoming/PT-0011.json"))
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "incoming" and body["bed_id"] is None
    assert "waiting list" in body["message"]
    types = [e["type"] for e in client.get("/api/events").json()]
    assert "bed_pressure" in types and "arrived" in types


def test_duplicate_post_is_409(client):
    payload = load_json("incoming/PT-0011.json")
    client.post("/api/patients", json=payload)
    assert client.post("/api/patients", json=payload).status_code == 409


def test_bad_payloads_are_422(client):
    assert client.post("/api/patients", json={}).status_code == 422
    assert client.post("/api/patients", content=b"not json",
                       headers={"Content-Type": "application/json"}).status_code == 422


def test_discharge_hands_bed_to_waiting_patient(client):
    client.post("/api/patients", json=load_json("incoming/PT-0012.json"))  # ESI 3
    client.post("/api/patients", json=load_json("incoming/PT-0011.json"))  # ESI 2, more urgent
    r = client.post("/api/patients/PT-0005/discharge",
                    json={"reason": "Clinically ready", "disposition": "home", "clinician": "Dr. Test"})
    assert r.status_code == 200
    assert r.json()["bed_given_to"] == "PT-0011"

    pt11 = client.get("/api/patients/PT-0011").json()
    assert pt11["encounter"]["status"] == "admitted" and pt11["encounter"]["bed_id"] == "4W-05"
    pt5 = client.get("/api/patients/PT-0005").json()
    assert pt5["encounter"]["status"] == "discharged"
    assert pt5["encounter"]["bed_released_for"] == "PT-0011"
    assert pt5["encounter"]["disposition"] == "home"


def test_discharge_errors(client):
    assert client.post("/api/patients/PT-9999/discharge", json={"reason": "x"}).status_code == 404
    assert client.post("/api/patients/PT-0001/discharge", json={"reason": ""}).status_code == 422
    client.post("/api/patients/PT-0001/discharge", json={"reason": "ready"})
    assert client.post("/api/patients/PT-0001/discharge", json={"reason": "again"}).status_code == 409


def test_discharge_with_empty_waiting_list_leaves_bed_free(client):
    r = client.post("/api/patients/PT-0001/discharge", json={"reason": "ready"}).json()
    assert r["bed_given_to"] is None
    free = [b for b in client.get("/api/beds").json() if b["patient_id"] is None]
    assert [b["bed_id"] for b in free] == ["4W-01"]
    # next POST should take the free bed straight away
    r = client.post("/api/patients", json=load_json("incoming/PT-0011.json")).json()
    assert r["status"] == "admitted" and r["bed_id"] == "4W-01"


def test_reset_restores_seed(client):
    client.post("/api/patients", json=load_json("incoming/PT-0011.json"))
    client.post("/api/patients/PT-0001/discharge", json={"reason": "ready"})
    assert client.post("/api/reset").json() == {"beds": 10, "patients": 10}
    assert client.get("/api/patients", params={"status": "incoming"}).json() == []
    assert len(client.get("/api/patients", params={"status": "admitted"}).json()) == 10
    assert [e["type"] for e in client.get("/api/events").json()] == ["demo_reset"]


def test_ui_reset_button_and_discharge_form(client):
    r = client.post("/ui/patients/PT-0003/discharge", data={"reason": "ready", "disposition": "home"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.post("/ui/reset", follow_redirects=True)
    assert r.status_code == 200 and "Demo reset" in r.text
