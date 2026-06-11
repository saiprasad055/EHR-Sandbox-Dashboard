# EHR Sandbox Dashboard

Single **Prior Authorization** style UI. Each EHR loads **numbered FHIR test exports** from disk (`fhir_epic_output_1.json`, `fhir_ecw_output_2.json`, … under each vendor’s folder in `sample_outputs/`), plus a merged **`★ Master`** sample (`master_output_<ehr>.json`) that is the default user. No OAuth, no live FHIR from this app.

Folder layout and file prefixes are defined in **`config.py`** (`SANDBOX_ROOT`, defaulting to `sandbox_dashboard/sample_outputs`, `SANDBOX_FOLDERS`, `FHIR_OUTPUT_PREFIX`). Sample data layout:

```
sandbox_dashboard/
  sample_outputs/
    epic/      fhir_epic_output_<n>.json
    ecw/       fhir_ecw_output_<n>.json
    nextgen/   fhir_nextgen_output_<n>.json
    iknowmed/  fhir_ikm_output_<n>.json
  master_output_<ehr>.json
```

## Quick start

```bash
cd ~/sandbox_dashboard
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5050**.

## Updating data

Regenerate or copy `fhir_*_output_<n>.json` files into the matching `sample_outputs/<ehr>/` folder, then reload the workspace (or switch EHR / test user in the UI). The `/api/context` endpoint accepts `ehr` and optional `slot` query parameters (`slot=master` for the merged sample).
