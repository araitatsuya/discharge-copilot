"""Shared test fixtures. Each test gets its own throwaway SQLite file, so tests
never touch hospital.db and never affect each other."""
import copy
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import config, db, llm
from app.main import app

DATA = config.DATA_DIR


@pytest.fixture(autouse=True)
def no_real_openai(monkeypatch):
    """Tests never call OpenAI. A test that wants an AI answer replaces llm.complete_json itself."""
    def offline(*args, **kwargs):
        raise llm.AIUnavailable("OpenAI disabled in tests")
    monkeypatch.setattr(llm, "complete_json", offline)


@pytest.fixture()
def session(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.create_tables()
    with Session(db.get_engine()) as s:
        yield s


@pytest.fixture()
def client(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    with TestClient(app) as c:  # "with" runs startup, which seeds the empty test DB
        yield c


def load_json(relative: str) -> dict:
    return json.loads((DATA / relative).read_text())


@pytest.fixture()
def minimal_patient() -> dict:
    """The smallest record the schema accepts: every optional field is null or empty."""
    return {
        "patient_id": "PT-0099",
        "mrn": "MRN-999999",
        "demographics": {"name": "Min Imal", "age": 40, "sex": "X"},
        "pcp": None,
        "encounter": {"encounter_id": "E-9999", "status": "incoming", "reason_for_admission": "Observation"},
        "triage": None,
        "clinical_status": {"stability": "stable"},
        "discharge_draft": None,
    }


@pytest.fixture()
def incoming_patient() -> dict:
    return copy.deepcopy(load_json("incoming/PT-0011.json"))

