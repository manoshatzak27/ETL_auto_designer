# OMOP concept matching on a standard-only Athena vocabulary

Two things live here:

1. **A dockerized Postgres** holding the OMOP Athena vocabulary, filtered down
   to **standard and valid concepts only**.
2. **A multi-stage matching pipeline** that maps source column names and
   descriptions onto concepts in that vocabulary.

This is a separate stack from `../ETL_auto_designer`, which runs its own
**unfiltered** OMOP database on host port 5432 (container `omop-postgres`).
This one uses a different container name, volume, and host port (`5433`) so
both can run side by side.

---

# Part 1 — the vocabulary database

## What "filtered" means

- `vocab.concept` contains only rows where `standard_concept = 'S'` **and**
  `invalid_reason IS NULL` — enforced by CHECK constraints, not just by how
  the data was loaded.
- Every other vocab table (`concept_relationship`, `concept_synonym`,
  `concept_ancestor`, `drug_strength`) is filtered so that **every**
  `concept_id` it references (both endpoints of a relationship, ancestor
  *and* descendant, drug *and* ingredient *and* unit concepts) survived that
  filter. Nothing in this database points at a non-standard or invalid
  concept.
- `vocabulary`, `domain`, `concept_class`, `relationship` are small lookup
  tables and are loaded in full — their own `*_concept_id` columns
  reference OMOP metadata concepts, which are intentionally non-standard
  classification concepts, so those specific columns have no FK to
  `vocab.concept`.
- Out of ~10.0M raw concepts in this bundle, ~3.5M are standard + valid.
  Dependent tables shrink proportionally more since *both* endpoints must
  survive.

Actual final row counts from the last load:

| table                  | rows       |
|------------------------|------------|
| `concept`              | 3,518,557  |
| `concept_relationship` | 22,063,876 |
| `concept_ancestor`     | 30,966,327 |
| `concept_synonym`      | 2,586,198  |
| `drug_strength`        | 2,944,760  |
| `vocabulary`           | 131        |
| `domain`               | 50         |
| `concept_class`        | 433        |
| `relationship`         | 722        |

Total on-disk size after cleanup: ~9.1GB.

## Which vocabularies actually survive

This is worth internalizing before using the pipeline, because it is a direct
and unavoidable consequence of the standard-only rule:

| vocabulary | standard concepts here |
|------------|------------------------|
| SNOMED     | 349,211 |
| RxNorm     | 157,190 |
| LOINC      | 119,029 |
| NDC        | 12,243  |
| HCPCS      | 8,847   |
| **ICD10CM, ICD9CM, CPT4, ICD10, ATC** | **0** |

Source vocabularies are non-standard *by definition* — their role in OMOP is
to be mapped away from — so filtering removed all of them. See
"Source-vocabulary codes" under Limitations for what that means in practice.

## Prerequisites

- Docker + Docker Compose.
- The Athena "ALL" vocabulary export already sitting in `./athena_ALL/`
  (tab-delimited `CONCEPT.csv`, `CONCEPT_RELATIONSHIP.csv`, etc. — already
  present in this directory).

## Loading

```bash
./load_vocab.sh
```

This starts the `postgres` container (`docker compose up -d`) and then runs
the SQL steps in `sql/` in order, against the mounted `athena_ALL` files:

1. `01_staging_schema.sql` — raw, unconstrained staging tables (all `text`
   columns, one per Athena CSV).
2. `02_load_staging.sql` — server-side `COPY` of each CSV into staging.
3. `03_final_schema.sql` — final `vocab` schema tables, with PKs/FKs/CHECKs.
4. `04_load_filtered.sql` — filters staging into `vocab` (the standard +
   valid rule).
5. `05_indexes.sql` — lookup indexes (concept_code, domain_id,
   vocabulary_id, concept_class_id, FK join columns).
6. `06_cleanup.sql` — drops the `staging` schema, `VACUUM ANALYZE`s
   everything, prints final row counts and a sanity check (a count of any
   non-standard/invalid rows in `vocab.concept`, which must be 0).

It's safe to re-run `./load_vocab.sh` — every step drops and recreates its
own tables first.

Loading ~6.5GB of raw CSVs (concept ≈1.3GB, concept_relationship ≈2.7GB,
concept_ancestor ≈1.8GB, concept_synonym ≈0.35GB, drug_strength ≈0.15GB)
takes a while — expect somewhere in the ballpark of 10-20 minutes depending
on disk speed, most of it in the `concept_relationship` and
`concept_ancestor` filter joins.

`docker-compose.yml` sets `shm_size: "1gb"` on the container — Docker's
64MB default `/dev/shm` is too small for Postgres to `VACUUM ANALYZE`
tables this large (fails with "could not resize shared memory segment",
which looks like a disk-space error but isn't — real disk usage stays
well under it).

The SQL files are **piped to `psql` over stdin**, not read from a bind mount.
A `./sql:/sql` mount can keep serving a stale copy of a file that was
rewritten on the host, which silently runs the previous version of a script.

## Connecting

### psql

```bash
psql -h localhost -p 5433 -U vocab -d athena_vocab
```

(host/port/user/db come from `.env`; change `POSTGRES_PORT` there if 5433
is also taken). Once connected, the database's default `search_path`
includes the `vocab` schema, so `SELECT * FROM concept` etc. resolve
without qualification.

### pgAdmin

Register a new server (right-click **Servers → Register → Server...**) with:

| field                  | value          |
|-------------------------|---------------|
| Name (General tab)      | `Athena Vocab` (or anything) |
| Host name/address       | `localhost` (or `127.0.0.1`) |
| Port                    | `5433`        |
| Maintenance database    | `athena_vocab`|
| Username                | `vocab`       |
| Password                | `vocab_pwd`   |

(values come from `.env` — update here too if you changed them). This
assumes pgAdmin runs directly on this host (desktop app or local
`pgadmin4`). If pgAdmin itself runs in a *separate* Docker container,
`localhost` refers to that container, not this host — use
`host.docker.internal` (Linux: needs `--add-host=host.docker.internal:host-gateway`
on that container) or this machine's actual IP instead.

## Browsing the vocabulary

In pgAdmin's tree: **Servers → Athena Vocab → Databases → athena_vocab →
Schemas → vocab → Tables**. That's where all nine tables listed above
live (not under `public`). Right-click a table → **View/Edit Data → All
Rows**, or open the **Query Tool** on the `athena_vocab` database and run
e.g.:

```sql
SELECT concept_id, concept_name, domain_id, vocabulary_id
FROM concept
WHERE concept_name ILIKE '%aspirin%'
LIMIT 50;
```

The pipeline's own objects live in the **`pipeline`** schema alongside it.

---

# Part 2 — the matching pipeline

Maps `(column_name, column_description)` onto a standard OMOP concept, with a
confidence score and an accept / review / unmapped decision.

## Setup

```bash
./setup_pipeline.sh                      # database objects (~2 min)

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Optional — Stage 5 semantic matching. CPU build keeps this to ~250MB
# instead of the ~2.5GB CUDA default.
.venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch
.venv/bin/pip install -r requirements-embeddings.txt

.venv/bin/python -m pipeline.cli check
```

`setup_pipeline.sh` runs four more SQL steps on top of the loaded vocabulary:

| file | what it builds |
|------|----------------|
| `10_pipeline_extensions.sql` | `pg_trgm`, `fuzzystrmatch`, `unaccent`, `btree_gin`, plus the `pipeline_normalize()` function that Stage 1 matches on |
| `11_pipeline_schema.sql` | `pipeline.concept_search` materialized view, the IDF and embedding-cache tables, the Stage 7 log/feedback tables, the Stage 9 reporting views |
| `12_pipeline_indexes.sql` | GIN FTS index on `(domain_id, search_vector)`, GIN trigram index, exact-match and logging indexes |
| `13_pipeline_idf.sql` | `pipeline.token_idf` — 316,026 corpus token frequencies |

Re-running is safe. Derived structures (matview, IDF) are rebuilt; captured
history (mapping logs, manual mappings, embedding cache) is preserved.

Without `sentence-transformers` installed the pipeline still works — Stage 5
is skipped and its 20% weight is redistributed across the stages that did run,
rather than scoring every candidate zero.

## Usage

```bash
# one column, with full stage-by-stage explanation
.venv/bin/python -m pipeline.cli match \
    --name SBP --description "Systolic blood pressure (mmHg)"

# many columns; CSV needs a `name` header, optionally `description` and `table`
.venv/bin/python -m pipeline.cli batch columns.csv \
    --out mappings.csv --source-system my_ehr --report

# Stage 9 reporting for a run
.venv/bin/python -m pipeline.cli report --run 3

# record a human correction (Stage 1 short-circuits to it from then on)
.venv/bin/python -m pipeline.cli feedback \
    --column creat_mgdl --description "Serum creatinine mg/dL" \
    --concept 3016723 --source-system my_ehr

# abbreviations Stage 4 discovered on its own
.venv/bin/python -m pipeline.cli abbreviations

# after a vocabulary refresh (Step 8.3)
.venv/bin/python -m pipeline.cli refresh
```

Useful flags on any subcommand: `--no-embeddings` (skip Stage 5),
`--no-log` (do not write to the log tables), `--config FILE`, `-v`.

## How it works

| stage | module | what survives |
|-------|--------|---------------|
| 0 preprocess | `stage0_preprocess.py` | 1..N interpretations of the input |
| 1 exact / code | `stage1_exact.py` | a hit exits at 100, or nothing |
| 2 full-text search | `stage2_fts.py` | ≤30 candidates per interpretation |
| 3 weighted Jaccard | `stage3_jaccard.py` | ≤15 |
| 4 fuzzy | `stage4_fuzzy.py` | ≤10 |
| 5 embeddings | `stage5_embeddings.py` | re-scores; eliminates nothing |
| 6 composite | `stage6_composite.py` | one winner, with a status |
| 7 validate + log | `stage7_validate.py` | the status, made honest |
| 8 batch | `stage8_batch.py` | parallelism, caching, refresh |
| 9 report | `stage9_report.py` | statistics and monitoring |

Composite score = 35% FTS + 25% Jaccard + 20% fuzzy + 20% embedding, then
vocabulary and domain boosts. ≥90 auto-accepts, ≥70 goes to the review queue,
below that is unmapped (after one retry with the domain filter dropped).

### Design decisions worth knowing

Several places depart from a literal reading of the specification. Each is
commented in the source where it happens; the substantive ones:

- **Stage 0 detects units and dates before expanding abbreviations**, not
  after. `hr` is both "hour" and "heart rate", `ms` both "millisecond" and
  "multiple sclerosis". Resolving the unit reading first — using position
  (next to a number, inside brackets) rather than the dictionary — means the
  abbreviation dictionary only ever sees genuinely clinical tokens.

- **The unexpanded input is always kept as an extra interpretation.** The
  abbreviation dictionary is a heuristic (`map`, `ca` and `pt` are ordinary
  words too), so the literal string is searched alongside every expansion and
  the best-scoring interpretation wins.

- **Stage 2 uses a four-rung query ladder, scored in bands.** Raw
  `ts_rank_cd` is not comparable across queries, and for a one-word query
  against a vocabulary full of that word it saturates — every glucose concept
  scored a flat 100. What *is* meaningful is which rung a candidate reached
  (rung 0 = contains every term asked for), so the rung sets a score band and
  the relative rank orders within it. Rung 2 requires all words but drops the
  adjacency constraint, because OMOP writes "serum creatinine" as "Creatinine
  measurement, serum" and strict contiguity buried the correct concept.

- **Stage 1 ignores bare acronym probes shorter than six characters.** "EGFR"
  is an exact *synonym* of "EGFR gene variant measurement", so a column named
  `egfr` described as "Estimated glomerular filtration rate" was being
  auto-accepted as a genomics concept at confidence 100. The expansion is a
  probe in its own right, so nothing is lost.

- **Stage 1 treats the domain hint as a hard filter.** An exact string match
  in a domain the units contradict is not deterministic: "Glucose 120 mg/dL"
  matches the RxNorm drug ingredient "glucose" exactly.

- **A boost that applies uniformly to every candidate is skipped.** Step 2.1
  pre-filters to one domain, so by Stage 3 every candidate usually shares it;
  multiplying them all by 1.10 cannot reorder anything but does push most of
  the shortlist into the 0-100 cap, destroying the ordering. See
  `scoring.py`.

- **A missing stage score is not zero.** When a stage did not run for a
  candidate, Stage 6 renormalizes the remaining weights instead of scoring the
  gap as zero — 80 on three stages is 80, not 64.

- **Stage 3's IDF lookup stems through Postgres.** `pipeline.token_idf` is
  keyed by Snowball stems (`measur`, `solut`) because it was built from the
  same tsvectors Stage 2 searches, while the token side holds lemmas. They
  never line up by string equality, so lookups go through
  `ts_lexize('english_stem', ...)`. A protected phrase takes the document
  frequency of its rarest component.

- **Step 4.2 matches acronyms by initials, not by trigram.** "hr" and "heart
  rate" share almost no character windows; trigram similarity calls them
  unrelated. Discoveries land in `pipeline.discovered_abbreviation`.

## Tuning

Copy `pipeline.example.json` to `pipeline.json` and edit. Precedence:
`pipeline/config.py` defaults < that file < the `pipeline.scoring_weight`
table < CLI flags.

Site-specific dictionary entries go in `*_local` siblings under
`pipeline/resources/`, which are merged over the shipped defaults and survive
updates to them:

| shipped | your overrides |
|---------|----------------|
| `abbreviations.json` | `abbreviations_local.json` |
| `units.json` | `units_local.json` |
| `phrases.txt` | `phrases_local.txt` |
| `stopwords.json` | `stopwords_local.json` |

An abbreviation mapped to a **list of two or more expansions** is treated as
ambiguous: every reading is searched separately and the best one wins.

## The feedback loop

`pipeline.cli feedback` writes to `pipeline.manual_mapping`, which Stage 1
consults before anything else — a correction is never re-derived. The key is
the normalized form of the column, so it generalizes across trivial spelling
and punctuation differences.

Manual mappings are **scoped to a source system**. One recorded with
`--source-system my_ehr` applies only when matching is run with that same
`--source-system`; omit the flag on both sides for a global mapping. This
prevents one system's conventions leaking into another's.

`pipeline.cli weights` compares, for every column a human later corrected, the
per-stage scores the pipeline gave the concept it picked against those it gave
the concept the human chose. A stage that consistently over-rates the wrong
concept is a candidate for a lower weight. Suggestions are never applied
automatically.

## Choosing an embedding model

Two benchmarks live in `benchmarks/`, graded against `gold_set.csv` (50 cases:
45 realistic clinical column names, 5 lay paraphrases). Grading is by **name
rule**, not by concept_id — pinning one correct id per column does not work in
OMOP, where "Glucose measurement", "Glucose measurement, serum" and the LOINC
"Glucose [Mass/volume] in Serum or Plasma" are all defensible answers for the
same column, and `concept_ancestor` does not reliably link across vocabularies.

```bash
.venv/bin/python benchmarks/compare_models.py    # end to end
.venv/bin/python benchmarks/stage5_rerank.py     # the model in isolation
```

**Reranking the Stage 2 shortlist, embeddings only** (44 winnable cases;
"lexical" is the shortlist order as Stage 2 produced it):

| ranker | top-1 | MRR | ms/case |
|--------|-------|-----|---------|
| lexical (no embeddings) | 93.2% | 0.960 | — |
| all-MiniLM-L6-v2 | **97.7%** | **0.982** | 41 |
| MedEmbed-small-v0.1 | **97.7%** | 0.981 | 95 |
| pubmedbert-base-embeddings | 95.5% | 0.977 | 193 |

**The same models, end to end through the whole pipeline:**

| model | overall | literal | semantic | ms/col |
|-------|---------|---------|----------|--------|
| none (Stage 5 off) | 90.0% | 93.3% | 60.0% | 196 |
| all-MiniLM-L6-v2 | 90.0% | 93.3% | 60.0% | 233 |
| pubmedbert-base-embeddings | 90.0% | 93.3% | 60.0% | 422 |
| MedEmbed-small-v0.1 | 88.0% | 91.1% | 60.0% | 292 |

Two conclusions, both worth stating plainly because both are the opposite of
the obvious guess:

1. **A biomedical model does not beat general-purpose MiniLM here.**
   PubMedBERT is *worse* and 4.7× slower; MedEmbed ties and is 2.3× slower.
   Concept names are short noun phrases, which is exactly what MiniLM is
   trained for, whereas PubMedBERT-family models are trained on biomedical
   prose. MiniLM stays the default on the evidence.

2. **Embeddings help in isolation (+4.5pp) but not end to end (+0.0).** The
   reranking they do is reranking Stages 3 and 4 have already done: by the time
   Stage 5 sees the candidates, IDF-weighted Jaccard and fuzzy matching have
   usually already promoted an acceptable concept to the top. Sweeping the
   Stage 5 weight from 0.0 to 0.5 leaves end-to-end accuracy flat at 93.3% and
   degrades it at 0.5.

So Stage 5 earns its place only where the lexical stages are weak. If your
inputs are free-text clinical descriptions rather than column names, re-measure
before assuming any of this transfers — that is what the benchmarks are for.

Switching model is one config key plus a cache rebuild, since cached vectors
are keyed by model, dimension and document prefix:

```bash
# pipeline.json
{ "embedding_model": "abhinand/MedEmbed-small-v0.1" }

.venv/bin/python -m pipeline.cli refresh --full
```

`EMBEDDING_MODEL_PRESETS` in `pipeline/config.py` carries the instruction
prefix each model needs. BGE- and E5-family models (which most medical
embedding models are built on) are trained with a prefix on the *query* side
only and lose accuracy without it; `embedding_query_prefix` and
`embedding_document_prefix` handle that asymmetry.

## Testing

```bash
.venv/bin/python -m unittest discover -s tests -v
```

50 tests. The Stage 0-6 tests are pure and run anywhere; the integration tests
need the loaded database and skip themselves cleanly when it is unreachable.

## Measured behaviour

Against a 32-column synthetic EHR schema (vitals, labs, conditions,
encounters, one deliberate nonsense column), with embeddings on, 4 workers,
and no manual mappings recorded yet:

```
auto_accept  18  (56.2%)     ~330 ms/column
review       12  (37.5%)
unmapped      2  ( 6.2%)
```

30 of the 32 resolved to the correct or a defensible concept. Both genuinely
wrong answers were in the review queue rather than auto-accepted, and both had
the right concept visible in their candidate list. The two unmapped were the
nonsense column and "Length of stay", which has no standard concept.

Per-stage cost, once the embedding model is warm: FTS ~90ms, Jaccard ~45ms,
Stage 1 ~40ms, fuzzy ~18ms, embeddings ~30ms, preprocessing <1ms.

## Limitations

**Source-vocabulary codes cannot be resolved.** ICD-10, ICD-9, CPT-4 and ATC
concepts are non-standard by definition and so are absent (see the table in
Part 1). Step 1.1's fallback — following `concept_relationship / 'Maps to'` to
a standard replacement — cannot help either, because the deprecated source
concept that owns the outgoing edge was filtered out with everything else.
The pipeline is explicit about this rather than quietly falling through to a
name match: an extracted-but-unresolvable code is recorded as a validation
flag on the mapping. LOINC, SNOMED, RxNorm, NDC and HCPCS codes resolve
normally.

Resolving ICD/CPT codes needs the unfiltered vocabulary — either a second
database loaded without the standard/valid filter, or a small
source-concept-to-standard-concept mapping table extracted from the Athena
CSVs at load time. Both conflict with the standard-only rule this database was
built to enforce, so neither is done here.

**Deprecation checks are structurally trivial.** Step 6.3 verifies the
selected concept is not deprecated; here `vocab.concept` has a CHECK
constraint that `invalid_reason IS NULL`, so it cannot be. The check is kept
because it costs one indexed lookup and it is what makes the invariant an
assertion rather than an assumption.

**Lemmatization is rule-based by default.** `pip install spacy` plus a model
(or `scispacy` for the clinical pipeline) switches `text_utils.Lemmatizer` to
spaCy automatically. The built-in fallback handles irregular medical plurals
from a table and is deliberately conservative — over-stemming a rare token
destroys the IDF weight Stage 3 depends on.

**Stage 5 contributes little end to end on this workload** — see "Choosing an
embedding model" below for the measurements. It is kept on by default because
it is free insurance on inputs where the lexical stages have little to work
with, but disabling it (`--no-embeddings`) costs nothing measurable on
column-name matching and saves ~40 ms/column.

**Precision and recall in `report` are proxies.** There is no ground truth to
measure against, so precision is computed only over the mappings a human later
revisited, and is reported as NULL rather than 100% when there are none.

## Not included here

- `CONCEPT_CPT4.csv` / `cpt4.jar` are not loaded — that's not an OMOP
  table, it's the Athena CPT4-licensing utility that patches CPT4 names
  into `CONCEPT.csv` directly (needs a UMLS API key). This bundle's
  `CONCEPT.csv` already has no blank CPT4 names.
- No ETL generation. The pipeline decides which concept a column means; it
  does not write the transformation that populates the CDM table.
