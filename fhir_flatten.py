"""Map unified FHIR collections into the prior-auth style flat dashboard model."""

from __future__ import annotations

from datetime import date
from typing import Any

from fhir_utils import (
    best_address,
    bundle_entries,
    coverage_member_id,
    first_coding_text,
    format_address_fields,
    human_name,
    parse_ref,
    telecom_value,
)

_ALL_KEYS = [
    "first_name",
    "middle_name",
    "last_name",
    "gender",
    "date_of_birth",
    "age_years",
    "member_id",
    "address_line_1",
    "address_line_2",
    "city",
    "state",
    "zip_code",
    "phone_number",
    "drug_name",
    "dosing_schedule",
    "quantity",
    "dosing_form",
    "days_of_supply",
    "primary_diagnosis",
    "primary_icd_code",
    "primary_description",
    "secondary_diagnosis",
    "secondary_icd_code",
    "npi",
    "date_of_service",
    "provider_first_name",
    "provider_last_name",
    "provider_address_line",
    "provider_city",
    "provider_state",
    "provider_zip",
    "provider_phone",
    "provider_fax",
    "clinical_notes",
    "lab_reports",
    "is_substitution_allowed",
    "drug_instructions",
    "dispense_amount",
    "prescription_generic",
    "patient_mrn_provider",
]


def _age_years(birth_date: str) -> str:
    if not birth_date:
        return ""
    try:
        y, m, d = [int(x) for x in birth_date[:10].split("-")]
        today = date.today()
        age = today.year - y - ((today.month, today.day) < (m, d))
        return str(age)
    except (ValueError, IndexError):
        return ""


def _condition_from_reason_code(mr: dict | None) -> dict | None:
    if not mr:
        return None
    reasons = mr.get("reasonCode") or []
    if not reasons:
        return None
    rc = reasons[0]
    return {"resourceType": "Condition", "code": rc, "id": "reason-from-med-request"}


def _coding_system_lower(c: dict) -> str:
    return str(c.get("system") or "").lower()


def _best_icd_coding(codings: list[dict]) -> dict | None:
    """Pick the best ICD-10 / ICD-9 coding from Condition.code.coding."""
    if not codings:
        return None
    scored: list[tuple[int, dict]] = []
    for c in codings:
        sys = _coding_system_lower(c)
        prio = 99
        if "icd-10" in sys and "cm" in sys:
            prio = 0
        elif "icd-10" in sys or "icd/10" in sys:
            prio = 1
        elif "icd10" in sys:
            prio = 2
        elif "icd-9" in sys or "icd/9" in sys:
            prio = 3
        elif "icd" in sys:
            prio = 4
        scored.append((prio, c))
    scored.sort(key=lambda x: x[0])
    for prio, c in scored:
        if prio <= 4:
            return c
    return None


def _condition_has_icd(cond: dict | None) -> bool:
    if not cond or cond.get("resourceType") != "Condition":
        return False
    codings = (cond.get("code") or {}).get("coding") or []
    return _best_icd_coding(codings) is not None


def _icd_code_and_description(cond: dict | None) -> tuple[str, str, str]:
    """
    ICD code for the ICD column, paired clinical description, provenance hint.
    Uses the same ICD coding for both so the row is consistent.
    """
    if not cond:
        return "", "", ""
    code = cond.get("code") or {}
    codings = code.get("coding") or []
    icd = _best_icd_coding(codings)
    if icd:
        code_str = str(icd.get("code") or "").strip()
        display = str(icd.get("display") or "").strip()
        if not display:
            display = str(code.get("text") or "").strip() or first_coding_text(code) or code_str
        return code_str, display, "Condition.code.coding (ICD-10 / ICD-9)"
    text = str(code.get("text") or "").strip() or first_coding_text(code) or ""
    return "", text, "Condition.code (no ICD coding on resource)"


def _pick_conditions(conditions: list[dict], mr: dict | None = None) -> tuple[dict | None, dict | None]:
    """First / second Condition that actually carry an ICD coding; else legacy order."""
    icd_conds = [c for c in conditions if _condition_has_icd(c)]
    if len(icd_conds) >= 1:
        return icd_conds[0], icd_conds[1] if len(icd_conds) > 1 else None
    if not conditions:
        syn = _condition_from_reason_code(mr)
        return (syn, None) if syn else (None, None)
    return conditions[0], conditions[1] if len(conditions) > 1 else None


def _pick_medication_request(mrs: list[dict]) -> dict | None:
    for mr in mrs:
        st = mr.get("status")
        if st in (None, "active", "on-hold", "draft"):
            return mr
    return mrs[0] if mrs else None


def _dosage_lines(mr: dict | None) -> str:
    if not mr:
        return ""
    lines: list[str] = []
    for d in mr.get("dosageInstruction") or []:
        if d.get("text"):
            lines.append(str(d["text"]))
        else:
            parts = []
            if d.get("doseAndRate"):
                for dr in d["doseAndRate"]:
                    dq = dr.get("doseQuantity") or {}
                    if dq:
                        parts.append(f"{dq.get('value', '')} {dq.get('unit', '')}".strip())
            timing = d.get("timing") or {}
            if timing.get("repeat"):
                rep = timing["repeat"]
                if rep.get("frequency") and rep.get("period"):
                    parts.append(f"every {rep.get('period')} {rep.get('periodUnit', '')}")
            if parts:
                lines.append(" ".join(parts))
    return "\n".join(lines) if lines else ""


NOT_FOUND_PROV: dict[str, str] = {"resource": "not found", "path": "not found"}


def _init_provenance() -> dict[str, dict[str, str]]:
    return {k: dict(NOT_FOUND_PROV) for k in _ALL_KEYS}


def _sync_prov(
    prov: dict[str, dict[str, str]],
    key: str,
    value: Any,
    resource: str,
    path: str,
) -> None:
    if value is None or (isinstance(value, str) and not str(value).strip()):
        prov[key] = dict(NOT_FOUND_PROV)
    else:
        prov[key] = {"resource": resource, "path": path}


def _med_name_with_prov(mr: dict | None, med_by_id: dict[str, dict]) -> tuple[str, str, str]:
    if not mr:
        return "", "not found", "not found"
    cc = mr.get("medicationCodeableConcept")
    if cc:
        t = first_coding_text(cc)
        if t:
            return t, "MedicationRequest", "MedicationRequest.medicationCodeableConcept"
    mref = mr.get("medicationReference")
    if isinstance(mref, dict) and mref.get("display"):
        return str(mref["display"]), "MedicationRequest", "MedicationRequest.medicationReference.display"
    ref = parse_ref(mref)
    if ref and ref[0] == "Medication":
        m = med_by_id.get(ref[1])
        if m:
            return (
                first_coding_text(m.get("code")) or str(m.get("id", "")),
                "Medication",
                "Medication.code (via MedicationRequest.medicationReference)",
            )
    return "", "not found", "not found"


def _quantity_days_form(mr: dict | None) -> tuple[str, str, str]:
    qty, days, form = "", "", ""
    if not mr:
        return qty, days, form
    dr = mr.get("dispenseRequest") or {}
    q = dr.get("quantity") or {}
    if q:
        qty = str(q.get("value", ""))
        if q.get("unit"):
            form = str(q.get("unit"))
    dss = dr.get("expectedSupplyDuration") or {}
    if dss.get("value"):
        days = str(dss["value"])
    return qty, days, form


def _pick_encounter(mr: dict | None, encounters: list[dict]) -> dict | None:
    if not encounters:
        return None
    if mr:
        pref = parse_ref(mr.get("encounter"))
        if pref and pref[0] == "Encounter":
            eid = pref[1]
            for e in encounters:
                if str(e.get("id")) == str(eid):
                    return e
    return encounters[0]


def _location_has_address(loc: dict | None) -> bool:
    if not loc:
        return False
    return any(format_address_fields(best_address(loc)).values())


def _location_from_encounter(enc: dict | None, loc_by_id: dict[str, dict]) -> dict | None:
    if not enc:
        return None
    for loc_item in enc.get("location") or []:
        loc_ref = loc_item.get("location")
        ref_str = None
        if isinstance(loc_ref, dict):
            ref_str = loc_ref.get("reference")
        elif isinstance(loc_ref, str):
            ref_str = loc_ref
        pref = parse_ref(ref_str)
        if pref and pref[0] == "Location":
            loc = loc_by_id.get(pref[1])
            if loc:
                return loc
    return None


def _first_location_with_address(locations: list[dict]) -> dict | None:
    for loc in locations:
        if _location_has_address(loc):
            return loc
    return None


def _practitioner_from_med_encounter(
    mr: dict | None,
    enc: dict | None,
    pr_by_id: dict[str, dict],
) -> dict | None:
    if mr:
        for p in mr.get("performer") or []:
            ref = parse_ref(p)
            if ref and ref[0] == "Practitioner":
                pr = pr_by_id.get(ref[1])
                if pr:
                    return pr
        ref = parse_ref(mr.get("requester"))
        if ref and ref[0] == "Practitioner":
            pr = pr_by_id.get(ref[1])
            if pr:
                return pr
    if enc:
        for part in enc.get("participant") or []:
            ref = parse_ref(part.get("individual"))
            if ref and ref[0] == "Practitioner":
                pr = pr_by_id.get(ref[1])
                if pr:
                    return pr
    return next(iter(pr_by_id.values()), None) if pr_by_id else None


def _encounter_date(enc: dict | None) -> str:
    if not enc:
        return ""
    period = enc.get("period") or {}
    return str(period.get("start") or enc.get("meta", {}).get("lastUpdated") or "")[:10]


def _labs_and_reports(obs_bundles: list[dict], doc_refs: list[dict], diag_reports: list[dict]) -> str:
    parts: list[str] = []
    for o in obs_bundles[:8]:
        code = first_coding_text(o.get("code"))
        val = ""
        if o.get("valueQuantity"):
            q = o["valueQuantity"]
            val = f"{q.get('value', '')} {q.get('unit', '')}".strip()
        elif o.get("valueString"):
            val = str(o["valueString"])
        elif o.get("valueCodeableConcept"):
            val = first_coding_text(o["valueCodeableConcept"])
        if code or val:
            parts.append(f"{code}: {val}".strip(": "))
    for d in doc_refs[:5]:
        desc = d.get("description") or first_coding_text(d.get("type"))
        parts.append(f"Document: {desc}")
    for r in diag_reports[:5]:
        parts.append(f"Report: {r.get('code', {}).get('text') or first_coding_text(r.get('code'))}")
    return "\n".join(parts) if parts else ""


def _clinical_notes(mr: dict | None, enc: dict | None) -> str:
    bits: list[str] = []
    if mr and mr.get("note"):
        for n in mr["note"]:
            if n.get("text"):
                bits.append(str(n["text"]))
    if enc and enc.get("reasonCode"):
        bits.append(first_coding_text(enc["reasonCode"][0]) if enc["reasonCode"] else "")
    return "\n".join(b for b in bits if b)


def _substitution(mr: dict | None) -> str:
    if not mr:
        return ""
    sub = mr.get("substitution") or {}
    if sub.get("allowedBoolean") is True:
        return "Yes"
    if sub.get("allowedBoolean") is False:
        return "No"
    return ""


def _dispense_amount(mr: dict | None) -> str:
    if not mr:
        return ""
    dr = mr.get("dispenseRequest") or {}
    q = dr.get("quantity") or {}
    if q.get("value"):
        return f"{q.get('value')} {q.get('unit', '')}".strip()
    return ""


def _generic_brand(mr: dict | None) -> str:
    if not mr:
        return ""
    cat = mr.get("category") or []
    for c in cat:
        t = first_coding_text(c)
        if "community" in t.lower() or "discharge" in t.lower():
            continue
        if t:
            return t
    med_cat = mr.get("medicationReference", {})
    return med_cat.get("display") or ""


def _contained_practitioners(medication_requests: list[dict]) -> list[dict]:
    found: dict[str, dict] = {}
    for mr in medication_requests:
        for c in mr.get("contained") or []:
            if c.get("resourceType") == "Practitioner" and c.get("id"):
                found[str(c["id"])] = c
    return list(found.values())


def flatten_fhir(
    patient: dict | None,
    conditions: list[dict],
    medication_requests: list[dict],
    medication_dispenses: list[dict],
    encounters: list[dict],
    practitioners: list[dict],
    locations: list[dict],
    observations: list[dict],
    document_references: list[dict],
    diagnostic_reports: list[dict],
    medications: list[dict],
    coverages: list[dict] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    flat: dict[str, Any] = {k: "" for k in _ALL_KEYS}
    prov = _init_provenance()

    med_by_id = {str(m["id"]): m for m in medications if m.get("id")}
    practitioners = list(practitioners) + _contained_practitioners(medication_requests)
    pr_by_id = {str(p["id"]): p for p in practitioners if p.get("id")}
    loc_by_id = {str(loc["id"]): loc for loc in locations if loc.get("id")}

    if patient:
        gn, fn = human_name(patient.get("name"))
        parts_g = gn.split()
        flat["first_name"] = parts_g[0] if parts_g else ""
        _sync_prov(prov, "first_name", flat["first_name"], "Patient", "Patient.name.given (first token)")
        flat["middle_name"] = " ".join(parts_g[1:]) if len(parts_g) > 1 else ""
        _sync_prov(prov, "middle_name", flat["middle_name"], "Patient", "Patient.name.given (remaining tokens)")
        flat["last_name"] = fn
        _sync_prov(prov, "last_name", flat["last_name"], "Patient", "Patient.name.family")
        flat["gender"] = (patient.get("gender") or "").capitalize()
        _sync_prov(prov, "gender", flat["gender"], "Patient", "Patient.gender")
        flat["date_of_birth"] = patient.get("birthDate") or ""
        _sync_prov(prov, "date_of_birth", flat["date_of_birth"], "Patient", "Patient.birthDate")
        pa = format_address_fields(best_address(patient))
        flat["address_line_1"] = pa["line1"]
        _sync_prov(prov, "address_line_1", flat["address_line_1"], "Patient", "Patient.address.line")
        flat["address_line_2"] = pa["line2"]
        _sync_prov(prov, "address_line_2", flat["address_line_2"], "Patient", "Patient.address.line")
        flat["city"] = pa["city"]
        _sync_prov(prov, "city", flat["city"], "Patient", "Patient.address.city")
        flat["state"] = pa["state"]
        _sync_prov(prov, "state", flat["state"], "Patient", "Patient.address.state")
        flat["zip_code"] = pa["zip"]
        _sync_prov(prov, "zip_code", flat["zip_code"], "Patient", "Patient.address.postalCode")
        flat["phone_number"] = telecom_value(patient, "phone")
        _sync_prov(prov, "phone_number", flat["phone_number"], "Patient", "Patient.telecom (phone)")
        flat["patient_mrn_provider"] = patient.get("id") or ""
        _sync_prov(prov, "patient_mrn_provider", flat["patient_mrn_provider"], "Patient", "Patient.id")

    cov_member_id = ""
    for cov in coverages or []:
        cov_member_id = coverage_member_id(cov)
        if cov_member_id:
            break
    flat["member_id"] = cov_member_id
    _sync_prov(
        prov,
        "member_id",
        flat["member_id"],
        "Coverage",
        "Coverage.subscriberId | Coverage.identifier (member)",
    )

    mr_pick = _pick_medication_request(medication_requests) or (
        medication_dispenses[0] if medication_dispenses else None
    )
    pri, sec = _pick_conditions(conditions, mr_pick)
    if pri:
        icd_c, icd_desc, icd_path = _icd_code_and_description(pri)
        flat["primary_icd_code"] = icd_c
        flat["primary_diagnosis"] = icd_desc
        _sync_prov(
            prov,
            "primary_diagnosis",
            flat["primary_diagnosis"],
            "Condition",
            "Condition.code (ICD-linked display)",
        )
        _sync_prov(prov, "primary_icd_code", flat["primary_icd_code"], "Condition", icd_path)
        flat["primary_description"] = icd_desc
        _sync_prov(
            prov,
            "primary_description",
            flat["primary_description"],
            "Condition",
            "Condition.code (same row as primary ICD)",
        )
    if sec:
        icd_c2, icd_desc2, icd_path2 = _icd_code_and_description(sec)
        flat["secondary_icd_code"] = icd_c2
        flat["secondary_diagnosis"] = icd_desc2
        _sync_prov(
            prov,
            "secondary_diagnosis",
            flat["secondary_diagnosis"],
            "Condition",
            "Condition.code (ICD-linked display)",
        )
        _sync_prov(prov, "secondary_icd_code", flat["secondary_icd_code"], "Condition", icd_path2)

    mr = mr_pick
    enc = _pick_encounter(mr, encounters)
    pr = _practitioner_from_med_encounter(mr, enc, pr_by_id)

    if mr:
        dn, dn_res, dn_path = _med_name_with_prov(mr, med_by_id)
        flat["drug_name"] = dn
        _sync_prov(prov, "drug_name", flat["drug_name"], dn_res, dn_path)
        flat["dosing_schedule"] = _dosage_lines(mr)
        _sync_prov(
            prov,
            "dosing_schedule",
            flat["dosing_schedule"],
            "MedicationRequest",
            "MedicationRequest.dosageInstruction",
        )
        q, days, _form_unused = _quantity_days_form(mr)
        flat["quantity"] = q
        _sync_prov(
            prov,
            "quantity",
            flat["quantity"],
            "MedicationRequest",
            "MedicationRequest.dispenseRequest.quantity.value",
        )
        flat["days_of_supply"] = days
        _sync_prov(
            prov,
            "days_of_supply",
            flat["days_of_supply"],
            "MedicationRequest",
            "MedicationRequest.dispenseRequest.expectedSupplyDuration",
        )
        if not flat["dosing_form"] and mr.get("dosageInstruction"):
            di0 = (mr.get("dosageInstruction") or [{}])[0]
            dr_list = di0.get("doseAndRate") or []
            dq = {}
            for dr in dr_list:
                cand = dr.get("doseQuantity") or {}
                if cand.get("unit"):
                    dq = cand
                    break
            if not dq and dr_list:
                dq = (dr_list[0].get("doseQuantity") or {})
            flat["dosing_form"] = str(dq.get("unit") or first_coding_text(di0.get("route")) or "")
        _sync_prov(
            prov,
            "dosing_form",
            flat["dosing_form"],
            "MedicationRequest",
            "MedicationRequest.dosageInstruction[0].doseAndRate[].doseQuantity.unit | route",
        )
        flat["clinical_notes"] = _clinical_notes(mr, enc)
        _sync_prov(
            prov,
            "clinical_notes",
            flat["clinical_notes"],
            "MedicationRequest | Encounter",
            "MedicationRequest.note; Encounter.reasonCode",
        )
        flat["is_substitution_allowed"] = _substitution(mr)
        _sync_prov(
            prov,
            "is_substitution_allowed",
            flat["is_substitution_allowed"],
            "MedicationRequest",
            "MedicationRequest.substitution.allowedBoolean",
        )
        flat["drug_instructions"] = flat["dosing_schedule"]
        _sync_prov(
            prov,
            "drug_instructions",
            flat["drug_instructions"],
            "MedicationRequest",
            "MedicationRequest.dosageInstruction (mirrors dosing schedule)",
        )
        flat["dispense_amount"] = _dispense_amount(mr)
        _sync_prov(
            prov,
            "dispense_amount",
            flat["dispense_amount"],
            "MedicationRequest",
            "MedicationRequest.dispenseRequest.quantity (formatted)",
        )
        flat["prescription_generic"] = _generic_brand(mr)
        _sync_prov(
            prov,
            "prescription_generic",
            flat["prescription_generic"],
            "MedicationRequest",
            "MedicationRequest.category | medicationReference.display",
        )

    flat["date_of_service"] = _encounter_date(enc)
    dos_res, dos_path = "Encounter", "Encounter.period.start"
    if flat["date_of_service"]:
        _sync_prov(prov, "date_of_service", flat["date_of_service"], dos_res, dos_path)
    if not flat["date_of_service"] and mr and mr.get("authoredOn"):
        flat["date_of_service"] = str(mr["authoredOn"])[:10]
        _sync_prov(prov, "date_of_service", flat["date_of_service"], "MedicationRequest", "MedicationRequest.authoredOn")

    if pr:
        pfn, pln = human_name(pr.get("name"))
        pp = pfn.split()
        flat["provider_first_name"] = pp[0] if pp else ""
        _sync_prov(prov, "provider_first_name", flat["provider_first_name"], "Practitioner", "Practitioner.name.given")
        flat["provider_last_name"] = pln
        _sync_prov(prov, "provider_last_name", flat["provider_last_name"], "Practitioner", "Practitioner.name.family")
        flat["npi"] = ""
        for ident in pr.get("identifier") or []:
            if (ident.get("system") or "").endswith("npi") or (ident.get("type", {}).get("text") or "").upper() == "NPI":
                flat["npi"] = str(ident.get("value") or "")
                break
        if not flat["npi"]:
            for ident in pr.get("identifier") or []:
                if ident.get("value") and len(str(ident["value"])) == 10:
                    flat["npi"] = str(ident["value"])
                    break
        _sync_prov(prov, "npi", flat["npi"], "Practitioner", "Practitioner.identifier (NPI)")
        flat["provider_phone"] = telecom_value(pr, "phone")
        _sync_prov(prov, "provider_phone", flat["provider_phone"], "Practitioner", "Practitioner.telecom (phone)")
        flat["provider_fax"] = telecom_value(pr, "fax")
        _sync_prov(prov, "provider_fax", flat["provider_fax"], "Practitioner", "Practitioner.telecom (fax)")

    loc_res = _location_from_encounter(enc, loc_by_id)
    if not _location_has_address(loc_res):
        alt_loc = _first_location_with_address(locations)
        if alt_loc is not None:
            loc_res = alt_loc
    if loc_res:
        la = format_address_fields(best_address(loc_res))
        parts = [la["line1"], la["line2"]]
        flat["provider_address_line"] = ", ".join(p for p in parts if p)
        flat["provider_city"] = la["city"]
        flat["provider_state"] = la["state"]
        flat["provider_zip"] = la["zip"]
        _sync_prov(prov, "provider_address_line", flat["provider_address_line"], "Location", "Location.address.line")
        _sync_prov(prov, "provider_city", flat["provider_city"], "Location", "Location.address.city")
        _sync_prov(prov, "provider_state", flat["provider_state"], "Location", "Location.address.state")
        _sync_prov(prov, "provider_zip", flat["provider_zip"], "Location", "Location.address.postalCode")
    elif pr:
        pra = format_address_fields(best_address(pr))
        flat["provider_address_line"] = ", ".join(p for p in (pra["line1"], pra["line2"]) if p)
        flat["provider_city"] = pra["city"]
        flat["provider_state"] = pra["state"]
        flat["provider_zip"] = pra["zip"]
        _sync_prov(prov, "provider_address_line", flat["provider_address_line"], "Practitioner", "Practitioner.address.line")
        _sync_prov(prov, "provider_city", flat["provider_city"], "Practitioner", "Practitioner.address.city")
        _sync_prov(prov, "provider_state", flat["provider_state"], "Practitioner", "Practitioner.address.state")
        _sync_prov(prov, "provider_zip", flat["provider_zip"], "Practitioner", "Practitioner.address.postalCode")

    if not flat.get("provider_first_name") and mr:
        req = mr.get("requester")
        disp = ""
        if isinstance(req, dict):
            disp = str(req.get("display") or "").strip()
        if disp:
            bits = [b for b in disp.replace(",", " ").split() if b]
            if len(bits) == 1:
                flat["provider_last_name"] = bits[0]
            else:
                flat["provider_last_name"] = bits[0]
                flat["provider_first_name"] = " ".join(bits[1:])
            _sync_prov(prov, "provider_last_name", flat["provider_last_name"], "MedicationRequest", "MedicationRequest.requester.display (parsed)")
            _sync_prov(prov, "provider_first_name", flat["provider_first_name"], "MedicationRequest", "MedicationRequest.requester.display (parsed)")

    flat["lab_reports"] = _labs_and_reports(observations, document_references, diagnostic_reports)
    _sync_prov(
        prov,
        "lab_reports",
        flat["lab_reports"],
        "Observation | DocumentReference | DiagnosticReport",
        "Observation.value*; DocumentReference.description/type; DiagnosticReport.code",
    )

    flat["age_years"] = _age_years(str(flat["date_of_birth"]))
    _sync_prov(
        prov,
        "age_years",
        flat["age_years"],
        "Patient",
        "computed from Patient.birthDate (dashboard)",
    )
    return flat, prov

