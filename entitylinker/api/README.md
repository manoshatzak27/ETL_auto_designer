# 🌐 EntityLinker API

FastAPI service that exposes the `entitylinker` library over HTTP for mapping clinical phrases to standardized OMOP concepts. This README covers **only the API** living in this folder.
The `/api/conceptlink` endpoint returns the top‑K OMOP concepts for a query (with optional GPT reranking and a short justification).


## Project structure
```text
├── api/                          # FastAPI service
│   ├── api/                      # service package
│   │   ├── application.py        # FastAPI app instance
│   │   ├── client.py             # client helper
│   │   ├── concept_link.py       # route(s)
│   │   ├── dependencies.py       # settings
│   │   ├── schema.py             # pydantic models
│   │   └── __init__.py
│   ├── example_client.py         # API example usage
│   ├── main.py                   # API usage - CSV file as input
│   ├── README.md                 # API usage & endpoints
│   ├── requirements.txt          # API-only deps
│   └── pyproject.toml         
```

---

## 📦 Installation
> **Python 3.11+** required.

### Option A — Poetry (recommended)

```bash
poetry install
```

This installs the API package and depends on the core `entitylinker` from the repo root.

### Option B — Pip

```bash
pip install -e .
```
---
### 1. ✅ Requirements

* OMOP `CONCEPT.csv` (tab‑delimited). Place it at `entitylinker/data/CONCEPT.csv` **or** anywhere you reference in `config.yaml`.
* A YAML config pointing to your data directory and vocabularies.
* (Optional) `OPENAI_API_KEY` (in `.env` or environment) to enable reranking.

### 2. 📂 Prepare Config

Use the provided template and edit paths and vocabularies:

```yaml
# config.yaml
# Folder that contains OMOP files (tab-delimited):
#   - CONCEPT.csv  (required)
# Caches (created on first run):
#   - embeddings_<VOCABS>.npy
#   - lookup_<VOCABS>.csv
#   - faiss_<VOCABS>.index

data_dir: "./entitylinker/data"

# OMOP vocabulary IDs to include
vocabularies:
  - SNOMED
  - LOINC
```

The code locates the config via **either**:

* CLI flag: `--config /path/to/config.yaml`
* Environment variable: `ENTITYLINKER_CONFIG=/path/to/config.yaml`
---

### 3. 📂 Prepare .env

Copy `.env.example` → `.env` and set your OpenAI key **only if** you want reranking:

```bash
cp .env.example .env
# then edit .env:
OPENAI_API_KEY=sk-...
```

## ▶️ Run the server

### Using Poetry script

```bash
poetry run entitylinker-api --config ../config.yaml
```

### Using Uvicorn directly

```bash
# set config via env first:
export ENTITYLINKER_CONFIG=../config.yaml
uvicorn api.application:app --host 0.0.0.0 --port 8000 --reload
```

### Health check & docs

* Health: `GET /` → `{ "message": "EntityLinker API is running" }`
* Docs:   open `http://127.0.0.1:8000/docs`

---

## 🚏 Endpoints

### POST `/api/conceptlink`

Returns the top‑K candidate concepts for a given clinical query.

**Request body**

```json
{
  "query": "Heart Attack",
  "top_k": 5,
  "use_reranker": "true"
}
```

**Response body**

```json
{
  "conceptlinks": [
    {
      "concept_name": "Myocardial infarction",
      "concept_code": "22298006",
      "concept_id": 4329847,
      "domain": "Condition",
      "vocabulary_id": "SNOMED",
      "score": 0.95,
      "justification": "Matches the exact medical condition for 'Heart Attack'."
    }
  ]
}
```

**Notes**

* When `use_reranker=false`, `score` is the cosine similarity from FAISS and `justification` is a generic string.
* When `use_reranker=true`, `score` is the reranker confidence **normalized to 0–1** and `justification` is model‑generated.

---

## 🧪 Examples

### Python (requests)

```python
import os, requests

url = "http://127.0.0.1:8000/api/conceptlink"
payload = {"query": "heart attack", "top_k": 5, "use_reranker": False}
resp = requests.post(url, json=payload, timeout=60)
resp.raise_for_status()
print(resp.json())
```

### Example client script

```bash
# from this directory
python example_client.py -q "heart attack" -k 5 --url "http://127.0.0.1:8000/api/conceptlink"
python example_client.py -q "complete blood count" --reranker
---