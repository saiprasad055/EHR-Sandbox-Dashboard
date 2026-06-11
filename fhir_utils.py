"""Small FHIR R4 helpers shared by loaders and flattening."""

from __future__ import annotations

from typing import Any


def bundle_entries(data: dict | None) -> list[dict]:
    if not data or not isinstance(data, dict):
        return []
    if data.get("resourceType") == "Bundle":
        out: list[dict] = []
        for e in data.get("entry") or []:
            if not e:
                continue
            r = e.get("resource")
            if r:
                out.append(r)
        return out
    if data.get("resourceType"):
        return [data]
    return []


def first_coding_text(cc: dict | None, prefer: str = "display") -> str:
    if not cc:
        return ""
    if cc.get("text"):
        return str(cc["text"])
    codings = cc.get("coding") or []
    if codings:
        c = codings[0]
        return str(c.get(prefer) or c.get("display") or c.get("code") or "")
    return ""


def human_name(names: list | None) -> tuple[str, str]:
    if not names:
        return "", ""
    n = names[0]
    if n.get("family") or n.get("given"):
        given = " ".join(n.get("given") or [])
        family = str(n.get("family") or "")
        return given.strip(), family.strip()
    if n.get("text"):
        full = str(n["text"]).strip()
        bits = full.split()
        if len(bits) == 1:
            return bits[0], ""
        return bits[0], " ".join(bits[1:])
    return "", ""


def telecom_value(resource: dict, system: str) -> str:
    for t in resource.get("telecom") or []:
        if t.get("system") == system and t.get("value"):
            return str(t["value"])
    for t in resource.get("telecom") or []:
        if t.get("value"):
            return str(t["value"])
    return ""


def first_address(resource: dict) -> dict:
    addrs = resource.get("address")
    if isinstance(addrs, dict):
        return addrs
    if isinstance(addrs, list) and addrs:
        return addrs[0] if isinstance(addrs[0], dict) else {}
    return {}


def best_address(resource: dict | None) -> dict:
    """Prefer a home address when multiple Patient.address entries exist."""
    if not resource:
        return {}
    addrs = resource.get("address")
    if isinstance(addrs, dict):
        return addrs
    if not isinstance(addrs, list) or not addrs:
        return {}
    for pref in ("home", "physical"):
        for a in addrs:
            if isinstance(a, dict) and a.get("use") == pref:
                return a
    first = addrs[0]
    return first if isinstance(first, dict) else {}


def format_address_fields(addr: dict | None) -> dict[str, str]:
    """Normalize FHIR Address → line1, line2, city, state, zip (handles line as string or list)."""
    if not addr or not isinstance(addr, dict):
        return {"line1": "", "line2": "", "city": "", "state": "", "zip": ""}
    line1 = line2 = ""
    raw_line = addr.get("line")
    if isinstance(raw_line, list):
        line1 = str(raw_line[0]).strip() if raw_line else ""
        line2 = str(raw_line[1]).strip() if len(raw_line) > 1 else ""
    elif isinstance(raw_line, str) and raw_line.strip():
        line1 = raw_line.strip()
    elif isinstance(raw_line, dict):
        line1 = str(raw_line.get("text") or raw_line.get("value") or "").strip()
    city = str(addr.get("city") or "").strip()
    state = str(addr.get("state") or "").strip()
    z = str(addr.get("postalCode") or "").strip()
    if not line1 and addr.get("text"):
        parts = [p.strip() for p in str(addr["text"]).split(",") if p.strip()]
        if parts:
            line1 = parts[0]
            if not city and len(parts) >= 2:
                city = parts[1]
            if not state and len(parts) >= 3:
                state = parts[2]
            if not z and len(parts) >= 4:
                z = parts[3].replace("US", "").strip()
    return {"line1": line1, "line2": line2, "city": city, "state": state, "zip": z}


def identifier_member(patient: dict) -> str:
    """Prefer payer / member style identifiers when present."""
    for ident in patient.get("identifier") or []:
        typ = ident.get("type") or {}
        text = (typ.get("text") or "").upper()
        codings = typ.get("coding") or []
        codes = [c.get("code", "") for c in codings]
        if "um" in codes or "MB" in codes or "MEMBER" in text or "EXTERNAL" in text:
            if ident.get("value"):
                return str(ident["value"])
    for ident in patient.get("identifier") or []:
        if ident.get("value"):
            return str(ident["value"])
    return ""


def coverage_member_id(coverage: dict | None) -> str:
    """Insurance member id from a Coverage resource (subscriberId, then member identifier)."""
    if not coverage or not isinstance(coverage, dict):
        return ""
    sub = coverage.get("subscriberId")
    if sub:
        return str(sub)
    for ident in coverage.get("identifier") or []:
        typ = ident.get("type") or {}
        text = (typ.get("text") or "").upper()
        codes = [c.get("code", "") for c in (typ.get("coding") or [])]
        if "MB" in codes or "MEMBER" in text:
            if ident.get("value"):
                return str(ident["value"])
    for ident in coverage.get("identifier") or []:
        if ident.get("value"):
            return str(ident["value"])
    return ""


def parse_ref(ref: str | dict | None) -> tuple[str, str] | None:
    if not ref:
        return None
    if isinstance(ref, dict):
        ref = ref.get("reference") or ""
    if not isinstance(ref, str) or "/" not in ref:
        return None
    rt, rid = ref.split("/", 1)
    return rt, rid


def resource_by_id_from_bundles(bundles: list[dict], resource_type: str) -> dict[str, dict]:
    """Index resources of a type from multiple bundles or single resources."""
    out: dict[str, dict] = {}
    for b in bundles:
        for r in bundle_entries(b):
            if r.get("resourceType") == resource_type and r.get("id"):
                out[str(r["id"])] = r
    return out
