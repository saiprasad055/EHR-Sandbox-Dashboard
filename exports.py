"""Load flattened dashboard fields from per-user FHIR export JSON files (fhir_*_output_<n>.json)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from config import fhir_output_prefix, sandbox_dir, slot_export_path
from fhir_flatten import flatten_fhir
from fhir_utils import bundle_entries, human_name

# Combined "master" sample (merged richest resources across slots).
MASTER_SLOT = "master"
MASTER_LABEL = "\u2605 Master"
_DASHBOARD_DIR = Path(__file__).resolve().parent


def master_export_path(ehr: str) -> Path:
    return _DASHBOARD_DIR / f"master_output_{ehr.lower()}.json"


def _consume_fhir_block(block: Any) -> list[dict]:
    """Turn export node into a list of FHIR resources (handles {data: Bundle|Resource} or raw Bundle/Resource)."""
    if not isinstance(block, dict):
        return []
    inner = block.get("data") if isinstance(block.get("data"), dict) else block
    if not isinstance(inner, dict):
        return []
    rt = inner.get("resourceType")
    if rt == "Bundle":
        return bundle_entries(inner)
    if rt:
        return [inner]
    return []


def _merged_from_resources_dict(resources: dict[str, Any]) -> dict[str, Any]:
    """Epic / NextGen / iKnowMed exports: resources is a map of name -> bundle or {data: ...}."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for _key, block in (resources or {}).items():
        for r in _consume_fhir_block(block):
            rt = r.get("resourceType")
            if not rt:
                continue
            rid = str(r.get("id", ""))
            sig = (str(rt), rid)
            if sig in seen:
                continue
            seen.add(sig)
            buckets[str(rt)].append(r)

    patient = buckets["Patient"][0] if buckets.get("Patient") else None

    def take(rt: str) -> list[dict]:
        return list(buckets.get(rt, []))

    return {
        "patient": patient,
        "conditions": take("Condition"),
        "medication_requests": take("MedicationRequest"),
        "medication_dispenses": take("MedicationDispense"),
        "encounters": take("Encounter"),
        "practitioners": take("Practitioner"),
        "locations": take("Location"),
        "observations": take("Observation"),
        "document_references": take("DocumentReference"),
        "diagnostic_reports": take("DiagnosticReport"),
        "medications": take("Medication"),
        "coverages": take("Coverage"),
    }


def _iter_ecw_block_resources(block: dict[str, Any]) -> list[dict]:
    """ECW export blocks: each item's `raw` is often a search Bundle, not a single resource."""
    rw = block.get("raw")
    if not isinstance(rw, dict):
        return []
    rt = rw.get("resourceType")
    if rt == "Bundle":
        return bundle_entries(rw)
    if rt:
        return [rw]
    return []


def _merged_from_ecw_emr(raw: dict[str, Any]) -> dict[str, Any]:
    """ECW fhir_ecw_output_*.json — list of resource blocks with .raw FHIR or Bundle."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    patient: dict | None = None

    for block in raw.get("resources") or []:
        if not isinstance(block, dict):
            continue
        for r in _iter_ecw_block_resources(block):
            if not isinstance(r, dict) or not r.get("resourceType"):
                continue
            rt = str(r["resourceType"])
            if rt == "Patient":
                patient = r
            rid = str(r.get("id", ""))
            sig = (rt, rid)
            if sig in seen:
                continue
            seen.add(sig)
            buckets[rt].append(r)

    def take(rt: str) -> list[dict]:
        return list(buckets.get(rt, []))

    return {
        "patient": patient,
        "conditions": take("Condition"),
        "medication_requests": take("MedicationRequest"),
        "medication_dispenses": take("MedicationDispense"),
        "encounters": take("Encounter"),
        "practitioners": take("Practitioner"),
        "locations": take("Location"),
        "observations": take("Observation"),
        "document_references": take("DocumentReference"),
        "diagnostic_reports": take("DiagnosticReport"),
        "medications": take("Medication"),
        "coverages": take("Coverage"),
    }


def list_output_slots(ehr: str) -> list[int]:
    """Sorted slot numbers found for this EHR (e.g. [1, 2] from fhir_epic_output_1.json)."""
    ehr = ehr.lower()
    folder = sandbox_dir(ehr)
    prefix = fhir_output_prefix(ehr)
    rx = re.compile(rf"^{re.escape(prefix)}(\d+)\.json$", re.IGNORECASE)
    slots: list[int] = []
    if not folder.is_dir():
        return []
    for p in folder.glob(f"{prefix}*.json"):
        m = rx.match(p.name)
        if m:
            slots.append(int(m.group(1)))
    return sorted(set(slots))


def _patient_label_from_resources_dict(raw: dict[str, Any]) -> str:
    """Human name from Epic/NextGen/iKnowMed style `resources` map."""
    res = raw.get("resources") or {}
    if not isinstance(res, dict):
        return ""
    pb = res.get("patient") or res.get("Patient")
    if not isinstance(pb, dict):
        return ""
    inner = pb.get("data") if isinstance(pb.get("data"), dict) else pb
    if not isinstance(inner, dict) or inner.get("resourceType") != "Patient":
        return ""
    gn, fn = human_name(inner.get("name"))
    name = f"{gn} {fn}".strip()
    if name:
        return name
    if inner.get("text") and isinstance(inner["text"], dict):
        t = str(inner["text"].get("div") or "").strip()
        if t:
            import re

            m = re.search(r"HeaderText\">([^<]+)</div>", t)
            if m:
                return re.sub(r"<[^>]+>", " ", m.group(1)).replace("  ", " ").strip()
    return str(inner.get("id") or "")


def peek_user_preview(path: Path, ehr: str) -> tuple[str, str]:
    """(display_name, secondary_line) for home page / selector; light JSON peek only."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return path.stem, ""
    ehr = ehr.lower()
    if ehr == "ecw":
        ps = raw.get("patient_summary")
        if isinstance(ps, dict):
            name = str(ps.get("name") or "").strip()
            dob = str(ps.get("birthDate") or "").strip()
            if name:
                return name, dob
        return str(raw.get("patient_id") or path.stem), ""
    label = _patient_label_from_resources_dict(raw)
    if not label:
        label = str(raw.get("patient_id") or path.stem)
    dob = ""
    res = raw.get("resources") or {}
    if isinstance(res, dict):
        pb = res.get("patient") or res.get("Patient")
        if isinstance(pb, dict):
            inner = pb.get("data") if isinstance(pb.get("data"), dict) else pb
            if isinstance(inner, dict) and inner.get("birthDate"):
                dob = str(inner["birthDate"])[:10]
    return label, dob


def list_test_user_previews(ehr: str) -> list[dict[str, Any]]:
    """Picker entries: master sample first (default), then one per export slot."""
    ehr = ehr.lower()
    out: list[dict[str, Any]] = []
    mpath = master_export_path(ehr)
    if mpath.is_file():
        out.append(
            {
                "slot": MASTER_SLOT,
                "label": MASTER_LABEL,
                "detail": "",
                "filename": mpath.name,
                "is_master": True,
            }
        )
    for s in list_output_slots(ehr):
        path = slot_export_path(ehr, s)
        label, detail = peek_user_preview(path, ehr)
        out.append(
            {
                "slot": s,
                "label": label,
                "detail": detail,
                "filename": path.name,
                "is_master": False,
            }
        )
    return out


def _build_master_payload(ehr: str, slots: list[int]) -> dict[str, Any]:
    """Flatten the merged master_output_<ehr>.json sample."""
    path = master_export_path(ehr)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if ehr == "ecw":
        merged = _merged_from_ecw_emr(raw)
    else:
        merged = _merged_from_resources_dict(raw.get("resources") or {})
    flat, field_src = flatten_fhir(**merged)
    pid = raw.get("patient_id") or (merged.get("patient") or {}).get("id") or ""
    return {
        "flat": flat,
        "field_src": field_src,
        "patient_id": str(pid),
        "slot": MASTER_SLOT,
        "export_filename": path.name,
        "user_label": MASTER_LABEL,
        "available_slots": slots,
        "is_master": True,
    }


def load_ehr_from_export(ehr: str, slot: int | str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """
    Load the master sample or a fhir_*_output_<slot>.json for this EHR.

    `slot` may be an int, the string "master", or None. When None, the master
    sample is the default (falling back to the first slot if no master exists).

    Returns (
        {
            'flat': {...}, 'field_src': {...}, 'patient_id': str,
            'slot': int | "master", 'export_filename': str, 'user_label': str,
            'available_slots': list[int], 'is_master': bool,
        },
        None,
    ) or (None, error_message).
    """
    ehr = ehr.lower()
    slots = list_output_slots(ehr)
    has_master = master_export_path(ehr).is_file()

    want_master = False
    int_slot: int | None = None
    if slot is None or slot == "":
        want_master = has_master  # master is the default user
    elif str(slot).lower() == MASTER_SLOT:
        want_master = True
    else:
        try:
            int_slot = int(slot)
        except (TypeError, ValueError):
            int_slot = None

    if want_master and has_master:
        try:
            return _build_master_payload(ehr, slots), None
        except Exception as exc:  # noqa: BLE001
            return None, f"Failed to load {master_export_path(ehr).name}: {exc}"

    if not slots:
        return None, (
            f"No FHIR test exports matching {fhir_output_prefix(ehr)}*.json in {sandbox_dir(ehr)}. "
            "Add fhir_*_output_1.json, _2.json, … or set SANDBOX_ROOT in .env."
        )

    use_slot: int = slots[0]
    if int_slot is not None and int_slot in slots:
        use_slot = int_slot
    path = slot_export_path(ehr, use_slot)
    if not path.is_file():
        return None, f"Missing export file {path.name}."

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"Could not read {path}: {exc}"

    try:
        if ehr == "ecw":
            merged = _merged_from_ecw_emr(raw)
        else:
            merged = _merged_from_resources_dict(raw.get("resources") or {})
        flat, field_src = flatten_fhir(**merged)
    except Exception as exc:  # noqa: BLE001
        return None, f"Failed to map FHIR from {path.name}: {exc}"

    pid = raw.get("patient_id") or (merged.get("patient") or {}).get("id") or ""
    user_label = f"{flat.get('first_name', '')} {flat.get('last_name', '')}".strip() or str(pid) or path.stem

    return {
        "flat": flat,
        "field_src": field_src,
        "patient_id": str(pid),
        "slot": use_slot,
        "export_filename": path.name,
        "user_label": user_label,
        "available_slots": slots,
        "is_master": False,
    }, None
