"""AI discharge recommendation: rules filter, AI answer checking, fallback, commands.

OpenAI is never called here: conftest blocks it, and tests that need an AI answer
install a fake one with ai_says(...).
"""
import json
import shlex

import pytest

from app import llm, recommend
from tests.conftest import load_json

URL = "/api/patients/PT-0011/recommendation"


def ai_says(monkeypatch, answer):
    calls = []

    def fake(system, user, schema_name, schema):
        calls.append(json.loads(user))
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(llm, "complete_json", fake)
    return calls


def good_answer(pid="PT-0005", decision="discharge"):
    return {"decision": decision, "discharge_patient_id": pid if decision == "discharge" else None,
            "summary": "Cellulitis resolved on oral antibiotics; no pending results.",
            "reasons": ["Stable on room air", "Blood cultures final"],
            "risks": ["Confirm wound check on 10/14"],
            "ranking": [{"patient_id": pid, "rank": 1, "assessment": "Most ready"}]}


@pytest.fixture()
def full_with_waiting(client):
    r = client.post("/api/patients", json=load_json("incoming/PT-0011.json")).json()
    assert r["status"] == "incoming"
    return client


def curl_body(command: str) -> tuple[str, dict]:
    """Parse a server-built curl command the way a shell would: returns (url, json body)."""
    args = shlex.split(command.replace("\\\n", " "))
    return args[args.index("-X") + 2], json.loads(args[args.index("-d") + 1])


# ---------------------------------------------------------------- trigger

def test_post_when_full_returns_next_step(client):
    r = client.post("/api/patients", json=load_json("incoming/PT-0011.json")).json()
    assert r["next_step"] == "curl -X POST http://testserver/api/patients/PT-0011/recommendation"


def test_post_with_free_bed_has_no_next_step(client):
    client.post("/api/patients/PT-0001/discharge", json={"reason": "ready"})
    r = client.post("/api/patients", json=load_json("incoming/PT-0011.json")).json()
    assert r["status"] == "admitted" and "next_step" not in r


@pytest.mark.parametrize("pid, code", [("PT-9999", 404), ("PT-0001", 409)])  # unknown / not waiting
def test_recommendation_preconditions(full_with_waiting, pid, code):
    r = full_with_waiting.post(f"/api/patients/{pid}/recommendation")
    assert r.status_code == code and r.text.startswith(f"Error {code}")


def test_recommendation_only_when_full(full_with_waiting):
    # a free bed while someone is waiting can't normally happen; force it by discharging with nobody queued
    c = full_with_waiting
    c.post("/api/patients/PT-0001/discharge", json={"reason": "ready"})  # bed goes to PT-0011
    c.post("/api/patients", json=load_json("incoming/PT-0012.json"))   # full again, PT-0012 waits
    assert c.post("/api/patients/PT-0012/recommendation").status_code == 200


# ---------------------------------------------------------------- rules layer

def test_unsafe_patients_never_sent_as_candidates(full_with_waiting, monkeypatch):
    calls = ai_says(monkeypatch, good_answer())
    full_with_waiting.post(URL)
    sent = {c["patient_id"] for c in calls[0]["eligible_candidates"]}
    assert "PT-0006" not in sent and "PT-0009" not in sent  # IV + oxygen / BiPAP
    assert {"PT-0006", "PT-0009"} <= {c["patient_id"] for c in calls[0]["not_eligible"]}


def test_ai_input_has_no_names_or_free_text(full_with_waiting, monkeypatch):
    calls = ai_says(monkeypatch, good_answer())
    full_with_waiting.post(URL)
    sent = json.dumps(calls[0])
    assert "Linda Okafor" not in sent and "Grace Liu" not in sent  # names stay on our side
    assert "Discharge medications" not in sent                   # no free-text drafts in v1


def test_rules_fallback_picks_clean_control_patient(full_with_waiting):
    r = full_with_waiting.post(URL, params={"format": "json"}).json()  # AI disabled -> rules
    assert r["source"] == "rules" and "disabled in tests" in r["ai_error"]
    assert r["decision"] == "discharge" and r["discharge"]["patient_id"] == "PT-0005"


# ---------------------------------------------------------------- AI layer

def test_ai_answer_used_when_valid(full_with_waiting, monkeypatch):
    ai_says(monkeypatch, good_answer("PT-0010"))
    r = full_with_waiting.post(URL, params={"format": "json"}).json()
    assert r["source"] == "ai" and r["discharge"]["patient_id"] == "PT-0010"
    assert r["ranking"][0]["patient_id"] == "PT-0010"
    assert len(r["ranking"]) == 8  # AI ranked 1; the other 7 eligible patients are appended


@pytest.mark.parametrize("answer, problem", [
    (good_answer("PT-0009"), "not an eligible candidate"),   # unsafe patient (BiPAP)
    (good_answer("PT-4242"), "not an eligible candidate"),   # invented ID
    ({**good_answer(), "decision": "discharge_everyone"}, "unknown decision"),
    ({**good_answer(), "summary": "  "}, "empty summary"),
])
def test_bad_ai_answer_falls_back_to_rules(full_with_waiting, monkeypatch, answer, problem):
    ai_says(monkeypatch, answer)
    r = full_with_waiting.post(URL, params={"format": "json"}).json()
    assert r["source"] == "rules" and problem in r["ai_error"]
    assert r["discharge"]["patient_id"] == "PT-0005"


def test_ai_outage_falls_back_to_rules(full_with_waiting, monkeypatch):
    ai_says(monkeypatch, llm.AIUnavailable("OpenAI call failed: APITimeoutError"))
    text = full_with_waiting.post(URL).text
    assert "RULES ONLY (OpenAI call failed: APITimeoutError)" in text
    assert "DECISION: DISCHARGE PT-0005" in text


def test_ai_keep_waiting(full_with_waiting, monkeypatch):
    ai_says(monkeypatch, good_answer(decision="keep_waiting"))
    r = full_with_waiting.post(URL, params={"format": "json"}).json()
    assert r["decision"] == "keep_waiting" and r["discharge"] is None
    labels = [c["label"] for c in r["commands"]]
    assert labels[0].startswith("Ask again") and labels[1].startswith("Override")


def test_untrusted_ai_text_is_truncated(full_with_waiting, monkeypatch):
    ai_says(monkeypatch, {**good_answer(), "reasons": ["x" * 5000] * 50})
    r = full_with_waiting.post(URL, params={"format": "json"}).json()
    assert len(r["reasons"]) == recommend.MAX_ITEMS and all(len(x) <= recommend.MAX_TEXT for x in r["reasons"])


# ---------------------------------------------------------------- commands + audit

def test_text_output_has_runnable_discharge_command(full_with_waiting, monkeypatch):
    ai_says(monkeypatch, good_answer())
    text = full_with_waiting.post(URL).text
    assert "DECISION: DISCHARGE PT-0005 Linda Okafor (bed 4W-05)" in text
    assert "curl -X POST http://testserver/api/patients/PT-0005/discharge" in text


def test_command_actually_works_and_links_recommendation(full_with_waiting, monkeypatch):
    c = full_with_waiting
    ai_says(monkeypatch, good_answer())
    r = c.post(URL, params={"format": "json"}).json()
    url, body = curl_body(r["commands"][0]["command"])
    assert body["recommendation_id"] == r["recommendation_id"]
    done = c.post(url.replace("http://testserver", ""), json=body).json()
    assert done["bed_given_to"] == "PT-0011"
    discharged = next(e for e in c.get("/api/events").json() if e["type"] == "discharged")
    assert discharged["details"]["recommendation_id"] == r["recommendation_id"]


def test_command_survives_apostrophe_in_reason(full_with_waiting, monkeypatch):
    ai_says(monkeypatch, {**good_answer("PT-0010"), "summary": "Mr. O'Brien's withdrawal resolved; it's safe."})
    r = full_with_waiting.post(URL, params={"format": "json"}).json()
    _, body = curl_body(r["commands"][0]["command"])
    assert "O'Brien's" in body["reason"]


def test_recommendation_is_logged_and_changes_nothing(full_with_waiting, monkeypatch):
    c = full_with_waiting
    ai_says(monkeypatch, good_answer())
    before = c.get("/api/beds").json()
    rec = c.post(URL, params={"format": "json"}).json()
    assert c.get("/api/beds").json() == before
    ev = c.get("/api/events").json()[0]
    assert ev["type"] == "ai_recommendation" and ev["event_id"] == rec["recommendation_id"]
    assert ev["actor"] == {"kind": "ai", "name": llm.model_name()}
    assert ev["related_patient_id"] == "PT-0005"


def test_not_first_in_line_warning(client, monkeypatch):
    client.post("/api/patients", json=load_json("incoming/PT-0012.json"))  # ESI 3
    client.post("/api/patients", json=load_json("incoming/PT-0011.json"))  # ESI 2 -> ahead
    text = client.post("/api/patients/PT-0012/recommendation").text
    assert "PT-0011 Grace Liu is ahead in line" in text


def test_ui_ask_ai_page(full_with_waiting):
    r = full_with_waiting.post("/ui/patients/PT-0011/recommendation")
    assert r.status_code == 200 and "Accept: discharge PT-0005" in r.text


# ---------------------------------------------------------------- key loading

def test_missing_key_is_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(llm.config, "ROOT", tmp_path)  # no apikey.txt here
    assert llm.load_api_key() is None
