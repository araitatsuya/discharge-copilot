"""FastAPI app: JSON API under /api, server-rendered pages everywhere else.

Run:  uvicorn app.main:app --reload
Then: http://127.0.0.1:8000
"""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import Session, select

from app import config, db, recommend, services
from app.db import Bed, Patient
from app.schemas import DischargeRequest, PatientRecord
from app.services import Conflict, NotFound

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    with Session(db.get_engine()) as session:
        services.ensure_seeded(session, config.DATA_DIR)
    yield


app = FastAPI(title="Discharge Copilot", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.exception_handler(RequestValidationError)
async def _invalid(request: Request, exc: RequestValidationError):
    # FastAPI's default 422 echoes the bad input back; NaN/Infinity can't be encoded as JSON,
    # so that would turn a clean 422 into a 500. Report where and why, not the value.
    errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(NotFound)
async def _not_found(request: Request, exc: NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(Conflict)
async def _conflict(request: Request, exc: Conflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# ======================================================================= API

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/beds")
def list_beds(session: Session = Depends(db.get_session)):
    admitted = {p.bed_id: p for p in session.exec(select(Patient).where(Patient.status == "admitted")).all()}
    beds = session.exec(select(Bed).order_by(Bed.bed_id)).all()
    return [{"bed_id": b.bed_id, "room": b.room, "unit": b.unit, "features": b.features,
             "patient_id": admitted[b.bed_id].patient_id if b.bed_id in admitted else None,
             "patient_name": admitted[b.bed_id].name if b.bed_id in admitted else None}
            for b in beds]


@app.get("/api/patients")
def list_patients(status: str | None = None, session: Session = Depends(db.get_session)):
    query = select(Patient).order_by(Patient.patient_id)
    if status:
        query = query.where(Patient.status == status)
    return [{"patient_id": p.patient_id, "name": p.name, "status": p.status, "bed_id": p.bed_id,
             "esi_level": p.esi_level} for p in session.exec(query).all()]


@app.get("/api/patients/{patient_id}")
def get_patient(patient_id: str, session: Session = Depends(db.get_session)):
    patient = session.get(Patient, patient_id)
    if patient is None:
        raise NotFound(f"No patient {patient_id}.")
    return patient.record


@app.post("/api/patients", status_code=201)
def create_patient(record: PatientRecord, request: Request, session: Session = Depends(db.get_session)):
    result = services.admit(session, record)
    if result["status"] == "incoming":  # hospital full: point at the AI recommendation
        result["next_step"] = (f"curl -X POST {_base_url(request)}/api/patients/"
                               f"{result['patient_id']}/recommendation")
        result["message"] += " Ask the AI who to discharge: run the next_step command."
    return result


@app.post("/api/patients/{patient_id}/recommendation")
def recommend_discharge(patient_id: str, request: Request, format: str = "text",
                        session: Session = Depends(db.get_session)):
    """AI + rules recommendation for a waiting patient. Plain text by default (for the terminal);
    ?format=json for scripts. Changes nothing except writing an ai_recommendation event."""
    try:
        result = recommend.recommend(session, patient_id, _base_url(request))
    except (NotFound, Conflict) as exc:
        code = 404 if isinstance(exc, NotFound) else 409
        if format == "json":
            return JSONResponse(status_code=code, content={"detail": str(exc)})
        return PlainTextResponse(f"Error {code}: {exc}\n", status_code=code)
    if format == "json":
        return result
    return PlainTextResponse(recommend.render_text(result))


@app.post("/api/patients/{patient_id}/discharge")
def discharge_patient(patient_id: str, req: DischargeRequest, session: Session = Depends(db.get_session)):
    return services.discharge(session, patient_id, req)


@app.get("/api/events")
def list_events(limit: int = 50, session: Session = Depends(db.get_session)):
    return [e.to_json() for e in services.recent_events(session, limit)]


@app.get("/api/version")
def version(session: Session = Depends(db.get_session)):
    """Latest event id. The board polls this and reloads when it changes."""
    return {"latest_event_id": services.latest_event_id(session)}


@app.post("/api/reset")
def reset(session: Session = Depends(db.get_session)):
    return services.reset_demo(session, config.DATA_DIR)


# ======================================================================= UI

@app.get("/", response_class=HTMLResponse)
def board(request: Request, msg: str | None = None, kind: str = "ok", session: Session = Depends(db.get_session)):
    return templates.TemplateResponse(request, "board.html", {
        "s": services.board_summary(session),
        "events": services.recent_events(session, 30),
        "latest_event_id": services.latest_event_id(session),
        "msg": msg, "kind": kind,
    })


@app.get("/patients/{patient_id}", response_class=HTMLResponse)
def patient_page(request: Request, patient_id: str, session: Session = Depends(db.get_session)):
    patient = session.get(Patient, patient_id)
    if patient is None:
        return _back(f"No patient {patient_id}.", "error")
    events = [e for e in services.recent_events(session, 500)
              if patient_id in (e.patient_id, e.related_patient_id)]
    return templates.TemplateResponse(request, "patient.html", {
        "p": patient, "r": patient.record or {}, "events": events,
    })


@app.post("/ui/reset")
def ui_reset(session: Session = Depends(db.get_session)):
    counts = services.reset_demo(session, config.DATA_DIR)
    return _back(f"Demo reset: {counts['beds']} beds, {counts['patients']} patients loaded.")


@app.post("/ui/patients/{patient_id}/discharge")
def ui_discharge(patient_id: str, reason: str = Form(""), disposition: str = Form("home"),
                 clinician: str = Form("Demo clinician"), recommendation_id: str = Form(""),
                 session: Session = Depends(db.get_session)):
    try:
        req = DischargeRequest(reason=reason, disposition=disposition, clinician=clinician or "Demo clinician",
                               recommendation_id=recommendation_id or None)
        result = services.discharge(session, patient_id, req)
    except (NotFound, Conflict) as exc:
        return _back(str(exc), "error")
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _back(f"Not discharged. Check the form: {problems}", "error")
    return _back(result["message"])


@app.post("/ui/patients/{patient_id}/recommendation", response_class=HTMLResponse)
def ui_recommend(request: Request, patient_id: str, session: Session = Depends(db.get_session)):
    try:
        result = recommend.recommend(session, patient_id, _base_url(request))
    except (NotFound, Conflict) as exc:
        return _back(str(exc), "error")
    return templates.TemplateResponse(request, "recommendation.html", {
        "r": result, "text": recommend.render_text(result)})


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _back(msg: str, kind: str = "ok") -> RedirectResponse:
    return RedirectResponse("/?" + urlencode({"msg": msg, "kind": kind}), status_code=303)
