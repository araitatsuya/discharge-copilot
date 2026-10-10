"""Bed-flow logic: seed/reset, admit, discharge, and the bed-board summary.

Rules (Phase 1):
- A POSTed patient always arrives as "incoming". If a bed is free they get it
  immediately; otherwise they join the waiting list (event: bed_pressure).
- Discharging a patient frees their bed and hands it to the most urgent
  waiting patient: lowest ESI level first, then earliest arrival.
- Every state change writes an Event. Events are never edited.
"""
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import db, metrics
from app.db import Bed, Event, Patient
from app.schemas import DischargeRequest, PatientRecord


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


# ---------------------------------------------------------------- seed / reset

def load_record(path: Path) -> PatientRecord:
    return PatientRecord.model_validate(json.loads(path.read_text()))


def reset_demo(session: Session, data_dir: Path) -> dict:
    """Wipe the database and reload beds + admitted patients from data/.

    Files in data/incoming/ are NOT loaded: they are payloads for curl POST.
    """
    session.close()
    db.drop_tables()
    db.create_tables()

    beds_doc = json.loads((data_dir / "beds.json").read_text())
    unit = beds_doc.get("unit", "")
    for b in beds_doc.get("beds", []):
        session.add(Bed(bed_id=b["bed_id"], room=b.get("room", ""), unit=unit, features=b.get("features", [])))

    patient_files = sorted((data_dir / "patients").glob("*.json"))
    for path in patient_files:
        rec = load_record(path)
        session.add(_row_from_record(rec))

    _log(session, "demo_reset", reason=f"Loaded {len(beds_doc.get('beds', []))} beds and {len(patient_files)} patients from data/")
    session.commit()
    return {"beds": len(beds_doc.get("beds", [])), "patients": len(patient_files)}


def ensure_seeded(session: Session, data_dir: Path) -> None:
    """First run only: seed if the database has no beds yet."""
    db.create_tables()
    if session.exec(select(Bed)).first() is None:
        reset_demo(session, data_dir)


def _row_from_record(rec: PatientRecord) -> Patient:
    enc = rec.encounter
    return Patient(
        patient_id=rec.patient_id,
        name=rec.demographics.name,
        status=enc.status,
        bed_id=enc.bed_id,
        esi_level=rec.triage.esi_level if rec.triage else None,
        arrived_at=metrics.to_naive(rec.triage.arrived_at if rec.triage else enc.admit_at),
        admit_at=metrics.to_naive(enc.admit_at),
        discharged_at=metrics.to_naive(enc.discharged_at),
        record=rec.to_json(),
    )


# ---------------------------------------------------------------- admit

ARRIVAL_WINDOW = timedelta(hours=24)


def admit(session: Session, rec: PatientRecord) -> dict:
    try:
        return _admit(session, rec)
    except IntegrityError:
        # Two identical POSTs at the same moment: the database refuses the second one.
        session.rollback()
        raise Conflict(_duplicate_id_message(rec.patient_id))


def _duplicate_id_message(patient_id: str) -> str:
    return f"{patient_id} already exists. Use a new patient_id and mrn, or press Reset demo."


def _admit(session: Session, rec: PatientRecord) -> dict:
    if session.get(Patient, rec.patient_id) is not None:
        raise Conflict(_duplicate_id_message(rec.patient_id))

    t = now()
    rec = rec.model_copy(deep=True)
    notes = _sanitize_incoming(rec, t)
    patient = _row_from_record(rec)
    patient.arrived_at = patient.arrived_at or t
    session.add(patient)
    session.flush()  # takes SQLite's write lock, so the MRN check below can't race another POST
    _reject_duplicate_mrn(session, patient)
    _log(session, "arrived", patient_id=patient.patient_id,
         reason=_arrival_reason(rec), details={"esi_level": patient.esi_level})

    bed = first_free_bed(session)
    if bed is not None:
        _assign_bed(session, patient, bed, t, reason="Free bed available")
        message = f"{patient.patient_id} admitted to bed {bed.bed_id}."
    else:
        session.flush()
        position = [p.patient_id for p in waiting_list(session)].index(patient.patient_id) + 1
        total = len(session.exec(select(Bed)).all())
        _log(session, "bed_pressure", patient_id=patient.patient_id,
             reason=f"All {total} beds occupied. Added to waiting list (position {position}).",
             details={"waiting_position": position})
        message = f"Hospital full. {patient.patient_id} is on the waiting list (position {position})."

    session.commit()
    return {"patient_id": patient.patient_id, "status": patient.status, "bed_id": patient.bed_id,
            "message": message, "notes": notes}


def _sanitize_incoming(rec: PatientRecord, t: datetime) -> list[str]:
    """Drop fields only the app may set. Returns a note for each field that was ignored."""
    notes = []
    enc = rec.encounter
    for field in ("bed_id", "admit_at", "discharged_at", "disposition", "bed_released_for"):
        if getattr(enc, field) is not None:
            notes.append(f"Ignored encounter.{field}: set by the app, not the caller.")
            setattr(enc, field, None)
    enc.status = "incoming"

    # Trust a reported arrival time only if it is recent and not in the future,
    # so a bad clock or a typo can't move a patient up the waiting list.
    reported = metrics.to_naive(rec.triage.arrived_at) if rec.triage else None
    if reported is not None and not (t - ARRIVAL_WINDOW <= reported <= t):
        notes.append(f"Ignored triage.arrived_at {reported.isoformat()}: not within the last 24 h. Using server time.")
        rec.triage.arrived_at = None
    return notes


def _reject_duplicate_mrn(session: Session, patient: Patient) -> None:
    """One person, one active stay: same MRN can't be incoming/admitted twice."""
    mrn = (patient.record or {}).get("mrn")
    active = session.exec(select(Patient).where(Patient.status.in_(["incoming", "admitted"]),
                                                Patient.patient_id != patient.patient_id)).all()
    for other in active:
        if (other.record or {}).get("mrn") == mrn:
            session.rollback()
            raise Conflict(f"{mrn} is already registered as {other.patient_id} ({other.status}). "
                           f"Same person can't have two active stays.")


def _arrival_reason(rec: PatientRecord) -> str:
    parts = [rec.encounter.reason_for_admission]
    if rec.triage:
        parts.append(f"ESI {rec.triage.esi_level}")
        if rec.triage.chief_complaint:
            parts.append(rec.triage.chief_complaint)
    return " · ".join(parts)


def _assign_bed(session: Session, patient: Patient, bed: Bed, t: datetime, reason: str,
                related_patient_id: str | None = None) -> None:
    patient.status = "admitted"
    patient.bed_id = bed.bed_id
    patient.admit_at = t
    _update_encounter(patient, status="admitted", bed_id=bed.bed_id, admit_at=t.isoformat())
    session.add(patient)
    _log(session, "bed_assigned", patient_id=patient.patient_id, bed_id=bed.bed_id,
         related_patient_id=related_patient_id, reason=reason)


# ---------------------------------------------------------------- discharge

def discharge(session: Session, patient_id: str, req: DischargeRequest) -> dict:
    patient = session.get(Patient, patient_id)
    if patient is None:
        raise NotFound(f"No patient {patient_id}.")
    if patient.status != "admitted":
        raise Conflict(f"{patient_id} is '{patient.status}', only admitted patients can be discharged.")

    t = now()
    bed = session.get(Bed, patient.bed_id) if patient.bed_id else None
    queue = waiting_list(session) if bed is not None else []
    next_patient = queue[0] if queue else None

    patient.status = "discharged"
    patient.discharged_at = t
    _update_encounter(patient, status="discharged", discharged_at=t.isoformat(), disposition=req.disposition,
                      bed_released_for=next_patient.patient_id if next_patient else None)
    session.add(patient)
    _log(session, "discharged", patient_id=patient_id, bed_id=patient.bed_id,
         related_patient_id=next_patient.patient_id if next_patient else None,
         actor_kind="clinician", actor_name=req.clinician, reason=req.reason,
         details={"disposition": req.disposition,
                  "length_of_stay_days": metrics.length_of_stay_days(patient.admit_at, t),
                  "recommendation_id": req.recommendation_id})

    message = f"{patient_id} discharged ({req.disposition}). Bed {patient.bed_id} is free."
    if next_patient is not None:
        _assign_bed(session, next_patient, bed, t, related_patient_id=patient_id,
                    reason=f"Bed released by {patient_id}; most urgent on waiting list (ESI {next_patient.esi_level or '—'})")
        message = f"{patient_id} discharged ({req.disposition}). Bed {bed.bed_id} given to {next_patient.patient_id}."

    session.commit()
    return {"patient_id": patient_id, "status": "discharged", "bed_id": patient.bed_id,
            "bed_given_to": next_patient.patient_id if next_patient else None, "message": message}


# ---------------------------------------------------------------- queries

def occupied_bed_ids(session: Session) -> set[str]:
    rows = session.exec(select(Patient.bed_id).where(Patient.status == "admitted")).all()
    return {b for b in rows if b}


def first_free_bed(session: Session) -> Bed | None:
    taken = occupied_bed_ids(session)
    for bed in session.exec(select(Bed).order_by(Bed.bed_id)).all():
        if bed.bed_id not in taken:
            return bed
    return None


def waiting_list(session: Session) -> list[Patient]:
    """Incoming patients, most urgent first. Missing ESI sorts last; missing arrival time sorts last."""
    rows = session.exec(select(Patient).where(Patient.status == "incoming")).all()
    return sorted(rows, key=lambda p: (p.esi_level if p.esi_level is not None else 99,
                                       p.arrived_at or datetime.max))


def recent_events(session: Session, limit: int = 50) -> list[Event]:
    limit = max(1, min(limit, 500))
    return list(session.exec(select(Event).order_by(Event.id.desc()).limit(limit)).all())


def latest_event_id(session: Session) -> int:
    ev = session.exec(select(Event).order_by(Event.id.desc())).first()
    return ev.id if ev else 0


def board_summary(session: Session) -> dict:
    """Everything the bed board page needs, already safe to print (no None surprises)."""
    t = now()
    beds = session.exec(select(Bed).order_by(Bed.bed_id)).all()
    admitted = {p.bed_id: p for p in session.exec(select(Patient).where(Patient.status == "admitted")).all()}

    bed_rows = []
    for bed in beds:
        p = admitted.get(bed.bed_id)
        bed_rows.append({"bed": bed, "patient": _patient_card(p, t) if p else None})

    waiting = [{
        "patient_id": p.patient_id,
        "name": p.name,
        "esi_level": p.esi_level,
        "complaint": ((p.record.get("triage") or {}).get("chief_complaint")
                      or (p.record.get("encounter") or {}).get("reason_for_admission") or "—"),
        "waiting": metrics.format_hours(metrics.hours_between(p.arrived_at, t)),
    } for p in waiting_list(session)]

    discharged = session.exec(select(Patient).where(Patient.status == "discharged")
                              .order_by(Patient.discharged_at.desc())).all()
    occupied = len(admitted)
    return {
        "beds": bed_rows,
        "total_beds": len(beds),
        "occupied": occupied,
        "occupancy": metrics.format_percent(metrics.occupancy_rate(occupied, len(beds))),
        "is_full": len(beds) > 0 and occupied >= len(beds),
        "waiting": waiting,
        "discharged": discharged,
    }


def _patient_card(p: Patient, t: datetime) -> dict:
    rec = p.record or {}
    demo = rec.get("demographics") or {}
    enc = rec.get("encounter") or {}
    cs = rec.get("clinical_status") or {}
    results = rec.get("results") or []
    return {
        "patient_id": p.patient_id,
        "name": p.name,
        "age_sex": f"{demo.get('age', '?')}{demo.get('sex', '')}",
        "reason": enc.get("reason_for_admission") or "—",
        "los": metrics.format_days(metrics.length_of_stay_days(p.admit_at, t)),
        "expected_discharge": enc.get("expected_discharge_date") or "—",
        "stability": cs.get("stability") or "unknown",
        "on_iv": bool(cs.get("on_iv_therapy")),
        "oxygen": cs.get("oxygen") or "—",
        "has_draft": rec.get("discharge_draft") is not None,
        "pending_results": sum(1 for r in results if r.get("status") in ("pending", "preliminary")),
    }


# ---------------------------------------------------------------- internals

def _update_encounter(patient: Patient, **fields) -> None:
    # JSON columns only save when the attribute is reassigned, so copy, edit, reassign.
    record = copy.deepcopy(patient.record or {})
    record.setdefault("encounter", {}).update(fields)
    patient.record = record


def _log(session: Session, type_: str, *, patient_id: str | None = None, related_patient_id: str | None = None,
         bed_id: str | None = None, actor_kind: str = "system", actor_name: str | None = None,
         reason: str | None = None, details: dict | None = None) -> None:
    session.add(Event(at=now(), type=type_, patient_id=patient_id, related_patient_id=related_patient_id,
                      bed_id=bed_id, actor_kind=actor_kind, actor_name=actor_name, reason=reason,
                      details=details or {}))
