"""SQLite tables (via SQLModel).

The full validated patient JSON is stored in Patient.record. The other Patient
columns are copies of the fields we filter and sort on (status, bed, ESI...).
"""
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine

from app import config


class Bed(SQLModel, table=True):
    bed_id: str = Field(primary_key=True)
    room: str
    unit: str
    features: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class Patient(SQLModel, table=True):
    patient_id: str = Field(primary_key=True)
    name: str
    status: str = Field(index=True)  # incoming | admitted | discharged
    bed_id: str | None = Field(default=None, index=True)  # current bed, or last bed once discharged
    esi_level: int | None = None
    arrived_at: datetime | None = None
    admit_at: datetime | None = None
    discharged_at: datetime | None = None
    record: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Event(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    at: datetime
    type: str = Field(index=True)
    patient_id: str | None = None
    related_patient_id: str | None = None
    bed_id: str | None = None
    actor_kind: str = "system"  # clinician | system | ai
    actor_name: str | None = None
    reason: str | None = None
    details: dict = Field(default_factory=dict, sa_column=Column(JSON))

    @property
    def event_id(self) -> str:
        return f"EV-{self.id:04d}" if self.id is not None else "EV-????"

    def to_json(self) -> dict:
        return {
            "event_id": self.event_id,
            "at": self.at.isoformat(timespec="seconds"),
            "type": self.type,
            "patient_id": self.patient_id,
            "related_patient_id": self.related_patient_id,
            "bed_id": self.bed_id,
            "actor": {"kind": self.actor_kind, "name": self.actor_name},
            "reason": self.reason,
            "details": self.details or {},
        }


_engine = None


def set_db_path(path: Path) -> None:
    """Point the app at a different SQLite file (used by tests)."""
    global _engine
    _engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def get_engine():
    if _engine is None:
        set_db_path(config.DB_PATH)
    return _engine


def create_tables() -> None:
    SQLModel.metadata.create_all(get_engine())


def drop_tables() -> None:
    SQLModel.metadata.drop_all(get_engine())


def get_session():
    """FastAPI dependency: one session per request."""
    with Session(get_engine()) as session:
        yield session
