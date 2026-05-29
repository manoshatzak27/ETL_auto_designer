# 🧠 EntityLinker

**Semantic search for clinical NLP**: maps free‑text clinical phrases to standardized OMOP concepts (SNOMED, LOINC, RxNorm, …) using SapBERT embeddings and FAISS similarity search, with optional GPT‑based reranking for improved accuracy and short justifications.

This repository contains both a **Python library** (`entitylinker`) and a **FastAPI service** under `api/`.

---

## ✨ Features

* 🔍 Maps clinical phrases to OMOP concepts using semantic similarity
* 🤖 SapBERT embeddings (PubMed‑tuned) for biomedical terms
* ⚡ Fast approximate search with FAISS (CPU or CUDA)
* 🧠 **Optional GPT reranker** (OpenAI) adds reasoning + short justification text
* 💾 Caching: embeddings (`.npy`), lookup CSV, FAISS index under `entitylinker/data/`
* 🧩 Python library **plus** a FastAPI service under `api/` (**documented separately** in `api/README.md`)
* 🧪 Pytest suite in `tests/`

---

## 📦 Installation

> **Python 3.11+** required.

### Option A — Pip (requirements.txt)

```bash
pip install -r requirements.txt
```

This installs **only the dependencies**. If you also want to install this `entitylinker` as a package (optional), run one of:

```bash
pip install -e .   # editable install for local development
# or
pip install .      # regular install
```

### Option B — Poetry

```bash
poetry install
```

This installs the project **as the `entitylinker` package**.

> For the HTTP API, see `api/README.md` for its own environment, dependencies, and commands.
---

## Project structure

Project structure
-----------------

```text
.
├── api/                          # FastAPI service
│
├── entitylinker/
│   ├── linker/
│   │   ├── concept_linker.py     # SapBERT + FAISS engine
│   │   ├── reranker.py           # OpenAI reranker (optional)
│   │   └── __init__.py
│   ├── data/                     # OMOP + caches (prebuilt or created on first run)
│   │   ├── CONCEPT.csv
│   │   ├── embeddings_*.npy
│   │   ├── faiss_*.index
│   │   └── lookup_*.csv
│   ├── config.py                 # YAML config loader
│   └── __init__.py
│
├── tests/
│   ├── data/test_concepts.csv
│   └── test_linker.py
|
├── .env.example                  # template for .env
├── config.example.yaml           # template for config
├── config.container.yaml         # config used inside container
├── docker-compose.yaml
├── dockerfile                    # container build recipe
├── example.py                    # demo / CLI
├── pyproject.toml, poetry.lock
├── requirements.txt
└── README.md
```
---

## ⚙️ Usage

### 1. ✅ Requirements

* OMOP `CONCEPT.csv` (tab‑delimited). Place it at `entitylinker/data/CONCEPT.csv` **or** anywhere you reference in `config.yaml`.
* A YAML config pointing to your data directory and vocabularies.
* (Optional) `OPENAI_API_KEY` (in `.env` or environment) to enable reranking.

### 2. 📂 Prepare Config

Use the provided template and edit paths and vocabularies:

Copy `config.example.yaml` → `config.yaml` and set your data directory path and the vocabularies you are interested in:

```bash
cp config.example.yaml config.yaml
# then edit data_dir & vocabularies
```

The code locates the config via **either**:

* CLI flag: `--config /path/to/config.yaml`
* Environment variable: `ENTITYLINKER_CONFIG=/path/to/config.yaml`


### 3. 📂 Prepare .env

Copy `.env.example` → `.env` and set your OpenAI key **only if** you want reranking:

```bash
cp .env.example .env
# then edit .env:
OPENAI_API_KEY=sk-...
```

### 3. 🔍 Library usage (Python)

```python
from entitylinker.linker.concept_linker import ConceptLinker
from entitylinker.linker.reranker import Reranker

# Initialize: loads SapBERT, builds/loads FAISS & caches
linker = ConceptLinker(
    config_path="./config.yaml",            # or set ENV ENTITYLINKER_CONFIG
    batch_size=64,                           # tune for your machine
)

# Retrieve FAISS candidates
raw = linker.find_best_concept("heart attack", top_k=5)
for name, code, cid, domain, vocab, score in raw:
    print(f"{name}  [{vocab}:{code} | id={cid} | {domain}]  sim={score:.3f}")

# Optional: rerank with OpenAI
reranker = Reranker(model="gpt-4o")
ranked = reranker.rerank("heart attack", raw)
for item in ranked:
    print(f"{item['concept_name']}  [{item['vocab']}:{item['concept_code']}]  score={item['confidence_score']/10.0}")
    print("  ↳", item["justification"]) 
```

### 4. 🖥 CLI / Demo

```bash
# Single query
python example.py --config ./config.yaml -q "heart attack" -k 5

# Multiple queries
python example.py --config ./config.yaml -q "heart attack" -q "complete blood count" -k 10

# With reranker
python example.py --config ./config.yaml -q "complete blood count" --reranker
```

### 5. API (separate)

An HTTP API (FastAPI) has been developed in the `api/` directory. This top‑level README documents the **Python library** only. Please refer to **`api/README.md`** for endpoints, examples, and deployment instructions.

---

### Notes & Tips

* **First run can be slow**: embeddings + FAISS index are built and cached under `entitylinker/data/`.
* **CUDA vs CPU**: if CUDA is available, the model uses GPU; otherwise CPU.
* **Narrow vocabularies** to reduce RAM/build time (e.g., only `SNOMED`, `LOINC`).
* **Testing**: run `pytest -q` (uses `tests/test_linker.py`).

---


### 🐳 Docker
Run the FastAPI service in a container using Docker Compose. The compose file builds the image, exposes **port 8000**, starts the app via the `entitylinker-api` entrypoint, loads environment from `.env`, and mounts your config and data into the container.

#### Quick start
```bash
# From the repo root
docker compose up --build
```
or
```bash
docker build .
docker compose up -d
```

- **Config & data mounts** (from the compose file):

    - `./config.container.yaml` → `/app/config.yaml`

    - `./entitylinker/data` → `/data`
    Adjust these to your environment if needed. Using relative paths keeps the setup portable. 

- **Reranker key (optional)**: `.env` is automatically loaded by Compose; set `OPENAI_API_KEY=...` there to enable GPT-based reranking. 

- **Container config**: `config.container.yaml` defaults to `data_dir: /data` and enables only SNOMED & LOINC. Make adjustments vocabularies for your case.

When the container is up, open http://localhost:8000/docs for the interactive API (the library and the API are separate; see `api/README.md` for endpoint details).