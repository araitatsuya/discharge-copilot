"""Robustness tests: None / null / empty values and divide-by-zero.

This is the placeholder suite. Add a test here every time you find a crash on
odd input.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import config, metrics, services
from app.schemas import DischargeRequest, PatientRecord

ALL_PATIENT_FILES = sorted(config.DATA_DIR.glob("patients/*.json")) + sorted(config.DATA_DIR.glob("incoming/*.json"))


# ---------------------------------------------------------------- divide by zero

@pytest.mark.parametrize("num, den, expected", [
    (1, 0, 0.0),        # divide by zero
    (0, 0, 0.0),
    (None, 5, 0.0),     # missing numerator
    (5, None, 0.0),     # missing denominator
    (5, 10, 0.5),       # normal case still works
])
def test_safe_divide(num, den, expected):
    assert metrics.safe_divide(num, den) == expected


def test_occupancy_with_zero_beds_does_not_crash():
    assert metrics.occupancy_rate(0, 0) == 0.0
    assert metrics.format_percent(metrics.occupancy_rate(0, 0)) == "0%"


def test_board_summary_with_empty_database(session):
    """No beds, no patients: page data must still build (0 / 0 occupancy)."""
    s = services.board_summary(session)
    assert s["total_beds"] == 0
    assert s["occupancy"] == "0%"
    assert s["is_full"] is False
    assert s["beds"] == [] and s["waiting"] == []


# ---------------------------------------------------------------- None / missing times

def test_length_of_stay_none_admit_time():
    assert metrics.length_of_stay_days(None) is None
    assert metrics.format_days(None) == "—"


def test_length_of_stay_future_admit_time_is_zero_not_negative():
    assert metrics.length_of_stay_days(datetime.now() + timedelta(days=2)) == 0.0


def test_mixed_timezone_aware_and_naive_times_do_not_crash():
    aware = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 10, 9, 12, 0)
    assert metrics.length_of_stay_days(aware, naive) is not None
    assert metrics.hours_between(aware, naive) is not None


def test_hours_between_none():
    assert metrics.hours_between(None, datetime.now()) is None
    assert metrics.format_hours(None) == "—"


# ---------------------------------------------------------------- schema: null and empty

@pytest.mark.parametrize("path", ALL_PATIENT_FILES, ids=lambda p: p.name)
def test_every_canonical_file_matches_schema(path: Path):
    PatientRecord.model_validate(json.loads(path.read_text()))


def test_minimal_record_defaults_lists_to_empty(minimal_patient):
    rec = PatientRecord.model_validate(minimal_patient)
    assert rec.results == [] and rec.diagnoses == [] and rec.follow_up_orders == []
    assert rec.pcp is None and rec.triage is None and rec.discharge_draft is None


@pytest.mark.parametrize("field, value", [
    ("name", ""),       # empty string
    ("name", "   "),    # whitespace only
    ("name", None),     # null
    ("age", -1),        # impossible value
])
def test_bad_demographics_rejected(minimal_patient, field, value):
    minimal_patient["demographics"][field] = value
    with pytest.raises(ValidationError):
        PatientRecord.model_validate(minimal_patient)


def test_empty_object_rejected():
    with pytest.raises(ValidationError):
        PatientRecord.model_validate({})


def test_unknown_field_rejected(minimal_patient):
    minimal_patient["favourite_colour"] = "blue"  # typo-catcher: extra fields are refused
    with pytest.raises(ValidationError):
        PatientRecord.model_validate(minimal_patient)


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_discharge_reason_required(reason):
    with pytest.raises(ValidationError):
        DischargeRequest(reason=reason)


# ---------------------------------------------------------------- end to end with nulls

def test_board_and_patient_page_render_with_minimal_patient(client, minimal_patient):
    """A patient with nulls everywhere must not break either HTML page."""
    assert client.post("/api/patients", json=minimal_patient).status_code == 201
    assert client.get("/").status_code == 200
    page = client.get("/patients/PT-0099")
    assert page.status_code == 200
    assert "none on file" in page.text  # null PCP shown, not crashed


def test_waiting_list_sorts_missing_esi_last(client, minimal_patient, incoming_patient):
    client.post("/api/patients", json=minimal_patient)    # no triage -> ESI None
    client.post("/api/patients", json=incoming_patient)   # ESI 2
    waiting = client.get("/api/patients", params={"status": "incoming"}).json()
    assert {w["patient_id"] for w in waiting} == {"PT-0099", "PT-0011"}
    # discharge one patient: the bed must go to ESI 2, not to the patient with no ESI
    r = client.post("/api/patients/PT-0005/discharge", json={"reason": "ready"}).json()
    assert r["bed_given_to"] == "PT-0011"
