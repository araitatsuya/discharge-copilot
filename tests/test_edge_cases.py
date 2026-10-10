"""Regression tests for the edge cases found while probing Phase 1.

Numbers match the weakness table: #1 concurrent duplicates, #3 same MRN,
#5 NaN/Infinity, #7 arrival time, #8 discharge fields on POST, #9 form errors.
"""
import copy
import json
import threading
from datetime import datetime, timedelta

import pytest

from tests.conftest import load_json


def _patient(pid: str, mrn: str, **triage) -> dict:
    p = copy.deepcopy(load_json("incoming/PT-0011.json"))
    p["patient_id"], p["mrn"] = pid, mrn
    p["triage"].update(triage)
    return p


# ---------------------------------------------------------------- #1 retry storm

def test_concurrent_identical_posts_never_500(client):
    payload = load_json("incoming/PT-0011.json")
    codes, start = [], threading.Barrier(8)

    def send():
        start.wait()
        codes.append(client.post("/api/patients", json=payload).status_code)

    threads = [threading.Thread(target=send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201] + [409] * 7
    assert len(client.get("/api/patients", params={"status": "incoming"}).json()) == 1


# ---------------------------------------------------------------- #3 same person, new ID

def test_same_mrn_new_id_rejected(client):
    assert client.post("/api/patients", json=_patient("PT-0011", "MRN-100011")).status_code == 201
    r = client.post("/api/patients", json=_patient("PT-0021", "MRN-100011"))
    assert r.status_code == 409
    assert "PT-0011" in r.json()["detail"]


def test_same_mrn_as_admitted_seed_patient_rejected(client):
    r = client.post("/api/patients", json=_patient("PT-0021", "MRN-100001"))  # Maria Gonzalez, in bed 4W-01
    assert r.status_code == 409 and "PT-0001" in r.json()["detail"]


def test_same_mrn_allowed_after_discharge(client):
    client.post("/api/patients/PT-0001/discharge", json={"reason": "ready"})
    assert client.post("/api/patients", json=_patient("PT-0021", "MRN-100001")).status_code == 201


# ---------------------------------------------------------------- #5 NaN / Infinity

@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_infinity_rejected(client, bad):
    body = json.dumps(load_json("incoming/PT-0011.json")).replace('"temp_c": 39.1', f'"temp_c": {bad}')
    assert bad in body
    r = client.post("/api/patients", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert client.get("/api/patients/PT-0011").status_code == 404


# ---------------------------------------------------------------- #7 arrival time

def test_arrival_time_far_in_past_does_not_jump_queue(client):
    client.post("/api/patients", json=_patient("PT-0030", "MRN-30"))
    client.post("/api/patients", json=_patient("PT-0031", "MRN-31", arrived_at="1900-01-01T00:00:00"))
    r = client.post("/api/patients/PT-0001/discharge", json={"reason": "ready"}).json()
    assert r["bed_given_to"] == "PT-0030"


def test_arrival_time_in_future_not_trusted(client):
    future = (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
    r = client.post("/api/patients", json=_patient("PT-0032", "MRN-32", arrived_at=future)).json()
    assert any("arrived_at" in note for note in r["notes"])


def test_recent_arrival_time_is_kept(client):
    recent = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    client.post("/api/patients", json=_patient("PT-0033", "MRN-33", arrived_at=None))  # server time = now
    client.post("/api/patients", json=_patient("PT-0034", "MRN-34", arrived_at=recent))
    # PT-0034 really did arrive earlier (2 h ago) with the same ESI, so it is first in line
    r = client.post("/api/patients/PT-0001/discharge", json={"reason": "ready"}).json()
    assert r["bed_given_to"] == "PT-0034"


# ---------------------------------------------------------------- #8 discharge fields on POST

def test_discharge_fields_on_post_are_cleared(client):
    p = load_json("incoming/PT-0011.json")
    p["encounter"].update(discharged_at="2026-10-01T00:00:00", disposition="home", bed_released_for="PT-0001")
    r = client.post("/api/patients", json=p).json()
    enc = client.get("/api/patients/PT-0011").json()["encounter"]
    assert enc["status"] == "incoming"
    assert enc["discharged_at"] is None and enc["disposition"] is None and enc["bed_released_for"] is None
    assert any("discharged_at" in note for note in r["notes"])


# ---------------------------------------------------------------- #9 form error messages

def test_ui_bad_disposition_says_disposition(client):
    r = client.post("/ui/patients/PT-0003/discharge", data={"reason": "ready", "disposition": "morgue"},
                    follow_redirects=False)
    assert "disposition" in r.headers["location"]
    assert client.get("/api/patients/PT-0003").json()["encounter"]["status"] == "admitted"


def test_ui_empty_reason_still_says_reason(client):
    r = client.post("/ui/patients/PT-0003/discharge", data={"reason": "  "}, follow_redirects=False)
    assert "reason" in r.headers["location"]
