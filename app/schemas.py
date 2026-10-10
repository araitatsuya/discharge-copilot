"""Patient record schema v0.1 — the Pydantic version of data/schema_example.jsonc.

Every POST to /api/patients and every seed file is validated against PatientRecord.
Unknown fields are rejected (extra="forbid") so typos fail loudly instead of
being silently dropped.
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    # str_strip_whitespace: "   " becomes "" and then fails min_length=1
    # allow_inf_nan=False: NaN/Infinity can't be written back out as valid JSON
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True,
                              allow_inf_nan=False)


class Demographics(_Model):
    name: str = Field(min_length=1)
    age: int = Field(ge=0, le=130)
    sex: Literal["F", "M", "X"]
    preferred_language: str = "en"
    phone: str | None = None
    lives_alone: bool | None = None


class PrimaryCareProvider(_Model):
    name: str = Field(min_length=1)
    clinic: str | None = None
    phone: str | None = None


EncounterStatus = Literal["incoming", "admitted", "discharged"]
Disposition = Literal["home", "home_with_services", "skilled_nursing", "transfer"]


class Encounter(_Model):
    encounter_id: str = Field(min_length=1)
    status: EncounterStatus
    bed_id: str | None = None
    admit_at: datetime | None = None
    expected_discharge_date: date | None = None
    attending: str | None = None
    reason_for_admission: str = Field(min_length=1)
    discharged_at: datetime | None = None
    disposition: Disposition | None = None
    bed_released_for: str | None = None


class Triage(_Model):
    source: str = "ED"
    arrived_at: datetime | None = None
    esi_level: int = Field(ge=1, le=5)
    chief_complaint: str | None = None
    requested_unit: str | None = None


class Vitals(_Model):
    taken_at: datetime | None = None
    hr: int | None = None
    bp: str | None = None
    rr: int | None = None
    spo2: int | None = None
    temp_c: float | None = None


class ClinicalStatus(_Model):
    stability: Literal["unstable", "improving", "stable"]
    on_iv_therapy: bool = False
    oxygen: str | None = None
    last_vitals: Vitals | None = None


class Diagnosis(_Model):
    code: str | None = None
    description: str = Field(min_length=1)
    role: Literal["principal", "secondary"] = "secondary"
    new_this_admission: bool = False


class Result(_Model):
    result_id: str = Field(min_length=1)
    category: Literal["lab", "microbiology", "pathology", "imaging"]
    name: str = Field(min_length=1)
    ordered_at: datetime | None = None
    status: Literal["pending", "preliminary", "final", "cancelled"]
    resulted_at: datetime | None = None
    value: str | float | None = None
    unit: str | None = None
    reference_range: str | None = None
    flag: Literal["normal", "high", "low", "critical", "abnormal"] | None = None
    summary: str | None = None
    recommendation: str | None = None
    expected_final_at: datetime | None = None
    ordering_provider: str | None = None
    follow_up_owner: str | None = None


class HomeMedication(_Model):
    name: str = Field(min_length=1)
    dose: str | None = None
    route: str | None = None
    frequency: str | None = None


class InpatientMedication(HomeMedication):
    status: Literal["active", "held", "discontinued", "completed"]
    status_reason: str | None = None
    start_at: datetime | None = None
    changed_at: datetime | None = None


class FollowUpOrder(_Model):
    type: Literal["appointment", "referral", "lab_draw", "phone_call"]
    specialty: str | None = None
    with_: str | None = Field(default=None, alias="with")
    reason: str | None = None
    status: Literal["scheduled", "requested"]
    scheduled_for: datetime | None = None


class DischargeDraft(_Model):
    status: Literal["draft", "signed"] = "draft"
    author: str | None = None
    written_at: datetime | None = None
    text: str = ""


class PatientRecord(_Model):
    schema_version: str = "0.1"
    patient_id: str = Field(pattern=r"^PT-\d{4}$")
    mrn: str = Field(min_length=1)
    demographics: Demographics
    pcp: PrimaryCareProvider | None = None
    encounter: Encounter
    triage: Triage | None = None
    clinical_status: ClinicalStatus
    diagnoses: list[Diagnosis] = []
    results: list[Result] = []
    home_medications: list[HomeMedication] = []
    inpatient_medications: list[InpatientMedication] = []
    follow_up_orders: list[FollowUpOrder] = []
    discharge_draft: DischargeDraft | None = None

    def to_json(self) -> dict:
        """JSON-safe dict using the on-disk field names (e.g. "with", not "with_")."""
        return self.model_dump(mode="json", by_alias=True)


class DischargeRequest(_Model):
    reason: str = Field(min_length=1)
    disposition: Disposition = "home"
    clinician: str = Field(default="Demo clinician", min_length=1)
    recommendation_id: str | None = Field(default=None, pattern=r"^EV-\d{4,}$")  # links to an ai_recommendation event
