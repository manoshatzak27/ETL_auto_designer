# OMOP ETL Auto-Designer

A code-less, browser-based ETL builder that converts any flat source CSV into OMOP CDM compliant output tables. Users define all transformation logic through a 7-step wizard UI. OpenAI GPT-4o generates the transformation code automatically.

---

## Architecture

```
ETL_auto_designer/
├── backend/         FastAPI Python API + SQLite database
│   ├── app/
│   │   ├── main.py          FastAPI app entry point
│   │   ├── config.py        Settings (reads from .env)
│   │   ├── database.py      SQLAlchemy + SQLite
│   │   ├── models/          ORM models
│   │   ├── schemas/         Pydantic request/response schemas
│   │   ├── api/             Route handlers
│   │   ├── services/        Business logic
│   │   │   ├── schema_inferrer.py   Auto-detect CSV delimiter/encoding/columns
│   │   │   ├── code_generator.py    OpenAI GPT-4o prompt builder + code gen
│   │   │   └── etl_executor.py      Execute generated Python as subprocess
│   │   └── prompts/         Per-table prompt templates for OpenAI
│   ├── uploads/             Uploaded source CSVs and mapping files
│   ├── outputs/             Generated OMOP output CSVs per project
│   └── requirements.txt
│
└── frontend/        React + Vite + TailwindCSS wizard UI
    └── src/
        ├── pages/wizard/    7-step wizard pages
        ├── components/      Shared UI components
        ├── api/client.ts    Axios API client
        └── types/           TypeScript type definitions
```

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- An OpenAI API key (required for code generation)
- (Optional) The `omop-docker-package` EntityLinker service running at `localhost:8000` for AI concept search

---

## Quick Start

### 1. Backend Setup

```powershell
cd backend

# Copy and edit environment variables
copy .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...

# Install dependencies
pip install -r requirements.txt

# Start the API server
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

### 2. Frontend Setup

```powershell
cd frontend

npm install
npm run dev
```

The UI will be available at `http://localhost:5173`.

---

## Environment Variables (`backend/.env`)

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(required)* | OpenAI API key for GPT-4o code generation |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model to use |
| `ENTITYLINKER_URL` | `http://localhost:8000/api/conceptlink` | URL of the EntityLinker concept search API from `omop-docker-package` |
| `DATABASE_URL` | `sqlite:///./etl_designer.db` | SQLAlchemy database URL |
| `UPLOAD_DIR` | `./uploads` | Directory for uploaded source CSVs |
| `OUTPUT_DIR` | `./outputs` | Directory for generated OMOP output CSVs |

---

## Wizard Walkthrough

### Step 1 — Upload Source CSV
Drop or browse to your flat source CSV file. The system auto-detects:
- Delimiter (`,` `;` `\t` `|`)
- Encoding (`UTF-8`, `windows-1252`, `latin-1`, etc.)
- All column names and row count

### Step 2 — Person Table Mapping
- Select the patient ID column and transform (int(float), int, or str)
- Select the gender column and map source values to OMOP concept IDs (e.g. `1.0` → `8507` Male, `2.0` → `8532` Female)
- Select the date of birth column and set the date format

### Step 3 — Visit Occurrence
- Define one or more visit types (Onset, Follow-up, etc.)
- For each visit: select the date column, visit_concept_id, type_concept_id
- Optional visits are only created when the date column is non-empty

### Step 4 — Observation Period
- Set start and end date columns
- Configure the fallback when end date is missing

### Step 5 — Stem Table
- Upload the concept mapping CSVs from `omop-docker-package`:
  - `variable_mapping.csv`
  - `value_mapping.csv`
  - `variable_value_mapping.csv`
- Classify each source column into a visit timepoint group (e.g. "onset", "followup_10y")
- Add special overrides for specific variables (e.g. force `unit_concept_id=9580` for duration variables)

### Step 6 — Death Table
- Select the column and value that indicates a patient died
- Choose whether death date is estimated (onset + N years) or from a direct date column

### Step 7 — Generate & Execute
- Click **Generate ETL Code** — GPT-4o produces a complete standalone Python script
- Review the syntax-highlighted code
- Click **Execute ETL** — the script runs against your source file
- Download the output OMOP CSVs

---

## Generated Code Behaviour

The generated Python script:
- Reads `ETL_SOURCE_PATH`, `ETL_OUTPUT_DIR`, and `ETL_MAPPING_FILES` from environment variables
- Uses only `pandas`, `numpy`, and Python standard library (no database required)
- Implements `VariableConceptMapper` logic: resolves concept_id, value_as_concept_id, value_as_number from the 3 mapping CSVs
- Executes tables in dependency order: person → visit_occurrence → observation_period → stem_table → death
- Outputs semicolon-delimited (`;`) UTF-8 CSV files, one per OMOP table

---

## Reference Repositories (read-only)

- `omop-docker-package` — Provides the 3 concept mapping CSV files consumed in Step 5
- `VOLABIOS_Data_harmonization` — Reference ETL implementation; all transformation patterns in the prompt templates are derived from this codebase

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/projects/` | List all projects |
| POST | `/api/projects/` | Create project |
| GET | `/api/projects/{id}` | Get project |
| DELETE | `/api/projects/{id}` | Delete project |
| POST | `/api/projects/{id}/upload-source` | Upload source CSV |
| POST | `/api/projects/{id}/upload-mapping?mapping_type=...` | Upload concept mapping CSV |
| GET | `/api/projects/{id}/source-preview` | Preview first N rows |
| PATCH | `/api/projects/{id}/config` | Save a table config step |
| GET | `/api/projects/{id}/config/{table}` | Get a table config |
| POST | `/api/projects/{id}/generate` | Generate ETL code via OpenAI |
| POST | `/api/projects/{id}/concept-search` | AI concept search (EntityLinker proxy) |
| POST | `/api/projects/{id}/execute` | Execute generated ETL |
| GET | `/api/projects/{id}/execution-log` | Get last execution log |
| GET | `/api/projects/{id}/download/{filename}` | Download output CSV |
