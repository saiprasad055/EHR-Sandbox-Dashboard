"""Paths to sandbox folders and FHIR per-user export file naming (internal; not shown in the UI)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DASHBOARD_DIR = Path(__file__).resolve().parent
# Sample FHIR exports now live alongside the app in sandbox_dashboard/sample_outputs/<ehr>/.
_DEFAULT_ROOT = _DASHBOARD_DIR / "sample_outputs"

SANDBOX_ROOT = Path(os.environ.get("SANDBOX_ROOT", str(_DEFAULT_ROOT))).resolve()

# Subfolder under SANDBOX_ROOT for each EHR
SANDBOX_FOLDERS: dict[str, str] = {
    "epic": "epic",
    "ecw": "ecw",
    "nextgen": "nextgen",
    "iknowmed": "iknowmed",
}

# Files: fhir_epic_output_1.json, fhir_ecw_output_2.json, fhir_nextgen_output_1.json, fhir_ikm_output_1.json
FHIR_OUTPUT_PREFIX: dict[str, str] = {
    "epic": "fhir_epic_output_",
    "ecw": "fhir_ecw_output_",
    "nextgen": "fhir_nextgen_output_",
    "iknowmed": "fhir_ikm_output_",
}


def sandbox_dir(ehr: str) -> Path:
    return SANDBOX_ROOT / SANDBOX_FOLDERS[ehr.lower()]


def fhir_output_prefix(ehr: str) -> str:
    return FHIR_OUTPUT_PREFIX[ehr.lower()]


def slot_export_path(ehr: str, slot: int) -> Path:
    """Path to fhir_<vendor>_output_<slot>.json for this EHR."""
    return sandbox_dir(ehr) / f"{fhir_output_prefix(ehr)}{int(slot)}.json"
