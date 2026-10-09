# OMOP concept matching on a standard-only Athena vocabulary

Two things live here:

1. **A dockerized Postgres** holding the OMOP Athena vocabulary, filtered down
   to **standard and valid concepts only**.
2. **A multi-stage matching pipeline** that maps source column names and
   descriptions onto concepts in that vocabulary.

Both are containerized; Docker is the only prerequisite. From cold:

```bash
./load_vocab.sh        # load the vocabulary (10-20 min)
./setup_pipeline.sh    # build the pipeline's database objects (~2 min)
./pipeline.sh check    # builds the pipeline image, then verifies the setup
```

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
./setup_pipeline.sh      # database objects (~2 min)
./pipeline.sh check      # builds the image on first use, then verifies
```

`setup_pipeline.sh` runs four more SQL steps on top of the loaded vocabulary:

| file | what it builds |
|------|----------------|
| `10_pipeline_extensions.sql` | `pg_trgm`, `fuzzystrmatch`, `unaccent`, `btree_gin`, the `pipeline_normalize()` function Stage 1 matches on, and `pipeline_is_keyword_dump()` (see "Synonyms that are not synonyms") |
| `11_pipeline_schema.sql` | `pipeline.concept_search` materialized view, the IDF and embedding-cache tables, the Stage 7 log/feedback tables, the Stage 9 reporting views |
| `12_pipeline_indexes.sql` | GIN FTS index on `(domain_id, search_vector)`, GIN trigram index, exact-match and logging indexes |
| `13_pipeline_idf.sql` | `pipeline.token_idf` — 316,026 corpus token frequencies |

Re-running is safe. Derived structures (matview, IDF) are rebuilt; captured
history (mapping logs, manual mappings, embedding cache) is preserved.

## The pipeline container

`./pipeline.sh` is a wrapper for `docker compose run --rm pipeline`. Whatever
it is given goes to the CLI, so every command below is the documented
`python -m pipeline.cli …` with the prefix dropped. Four verbs are not CLI
subcommands and are handled by the entrypoint instead: `serve` (see "Serving it
over HTTP"), `test`, `python`, and `bash`.

| what | where |
|------|-------|
| image | built from `Dockerfile` on first use; `docker compose build pipeline` to rebuild |
| database | reached at `postgres:5432` over the compose network, not through the published host port |
| `./data` | mounted at `/app/data` — put input CSVs there, write outputs there, and they appear on the host |
| `./pipeline`, `./tests` | mounted **read-only** over the image's copy, so editing a stopword list or a threshold takes effect on the next run with no rebuild |
| `./benchmarks` | mounted read-write; the benchmark scripts emit CSVs next to their gold sets |
| model cache | named volume `new_concept_finding_hf_cache`, so the ~90MB MiniLM downloads once rather than per run |
| config overrides | `data/pipeline.json` (copy `pipeline.example.json`); the container points `PIPELINE_CONFIG` at it |

The image is ~2GB on disk, nearly all of it CPU torch. `WITH_EMBEDDINGS=0
docker compose build pipeline` drops that to ~310MB; the pipeline then behaves
as it does on any host without torch — Stage 5 is skipped and its 20% weight
is redistributed across the stages that did run, rather than scoring every
candidate zero.

The image also carries its own copy of the source, so `docker run
vocab-pipeline:latest …` works standalone with no bind mounts — the mounts are
a development convenience, not a requirement.

To work without Docker instead, the pipeline is an ordinary Python package:
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (plus
`--index-url https://download.pytorch.org/whl/cpu torch` and
`requirements-embeddings.txt` for Stage 5), then
`.venv/bin/python -m pipeline.cli …` against `localhost:5433`.

## Usage

```bash
# one column — name, description, or both (--name is optional if you give one)
./pipeline.sh match --name SBP --description "Systolic blood pressure (mmHg)"
./pipeline.sh match --description "Hair sample has been collected"

# the full per-stage breakdown of how that answer was reached
./pipeline.sh match --name SBP --description "..." --explain

# accept concepts from one domain only, and nothing else
./pipeline.sh --domain Measurement match --name height
./pipeline.sh --domain Measurement --domain Observation batch data/columns.csv

# which domains can be named, and how many concepts each one holds
./pipeline.sh domains

# interactive; the model loads once, so each query costs ms instead of ~10s
#   creat_mgdl | Serum creatinine mg/dL     name and description
#   creat_mgdl                              name only
#   | Serum creatinine mg/dL                description only
./pipeline.sh shell

# the same, interactively — `name | description`, or just free text.
# The connection and the embedding model open once, so after the first
# query each one costs ~250 ms instead of a ~10 s model load.
./pipeline.sh shell

# many columns; CSV needs a `name` header, optionally `description` and `table`
# paths are container paths — data/ is the same directory on both sides
./pipeline.sh batch data/columns.csv \
    --out data/mappings.csv --source-system my_ehr --report

# Stage 9 reporting for a run
./pipeline.sh report --run 3

# record a human correction (Stage 1 short-circuits to it from then on)
./pipeline.sh feedback \
    --column creat_mgdl --description "Serum creatinine mg/dL" \
    --concept 3016723 --source-system my_ehr

# abbreviations Stage 4 discovered on its own
./pipeline.sh abbreviations

# after a vocabulary refresh (Step 8.3)
./pipeline.sh refresh
```

Useful flags on any subcommand: `--domain DOMAIN` (see below),
`--no-embeddings` (skip Stage 5), `--no-log` (do not write to the log tables),
`--config FILE`, `-v`. They are **global** flags, so they go before the
subcommand.

## Restricting the search to a domain

`--domain` is a hard allow-list. Only concepts in the domains you name can come
back — from any stage, including the deterministic Stage 1 hits and the Stage
6.4 "retry without domain filters" fallback, neither of which any scoring
preference can reach.

```bash
./pipeline.sh domains                                   # the valid names, with counts
./pipeline.sh --domain Measurement match --name height
./pipeline.sh --domain measurement,observation shell     # case-insensitive, comma-separated
./pipeline.sh --domain 'Meas Value' match --name smoker  # quote the ones with a space
```

Inside `shell` it can be changed without paying the model load again:

```
column> :domain Measurement          accept only this domain from now on
column> :domain Measurement, Observation
column> :domain off                  drop the restriction
column> :domain                      show what is in force
```

Over HTTP it is per request — `"domains": ["Measurement"]` in the `/match` body
(omit the key to use the server's own setting, pass `[]` to lift it for one
request). In `data/pipeline.json` it is `"restrict_domains": ["Measurement"]`.

What it is good for, measured on the column `height`:

| | top candidates |
|---|---|
| unrestricted | **Normal height** (Observation / Clinical Finding), Height - finding, … |
| `--domain Measurement` | Patient height, **Body height**, Knee height, … |

That is the "variables and values" defect below. The restriction does not
*solve* it — it lets you state the answer you already know, which is the part
the pipeline cannot infer. A rheumatology study's lab columns are all
Measurement; saying so removes a whole class of wrong answer in one flag.

Three details worth knowing:

* **It is a filter, not a preference.** It never boosts and never penalizes, so
  within the allowed domains the ranking is exactly what it was.
* **It outranks the inferred domain hint.** A unit in the description infers
  Measurement (Step 0.3); `--domain Condition` wins, because an inference
  should not overrule an instruction. When the two do not overlap at all the
  restriction is used alone rather than intersecting to nothing.
* **A misspelled domain is an error, not an empty result.** Names are resolved
  against `vocab.domain` before anything runs, and a domain that holds no
  standard concept in this database (`Modifier`, `Place of Service`, every
  `Condition/Device`-style combination domain) is refused too — restricting to
  one could only ever return nothing. `./pipeline.sh domains` lists what is
  real: 33 of the 50 defined domains.

It applies to a stored manual mapping as well, which is the one place it can
surprise you: the mapping is skipped and a validation flag says so, on the
grounds that whoever set the restriction did so more recently than whoever
recorded the mapping.

## Serving it over HTTP

`pipeline/service.py` puts the same matcher behind two JSON endpoints, so
another application can map a whole column list in one call instead of shelling
out per column:

```bash
./pipeline.sh serve      # or: docker compose run --rm -p 8000:8000 pipeline serve
```

| endpoint | |
|---|---|
| `GET /health` | readiness, vocabulary version, concept count, whether Stage 5 is available, the domains it will accept |
| `POST /match` | `{"columns": [{"name", "description"?, "table"?}, ...], "domains"?, "shortlist"?, "source_system"?, "note"?, "candidates"?}` |

`/match` is Stage 8 — the same path `pipeline.cli batch` takes, logging to the
same tables — and answers with one object per column, in request order:
`status` (`auto_accept` / `review` / `unmapped` / `manual` / `error`),
`confidence`, the selected concept, and the top candidates.

```bash
curl -s localhost:8000/match -H 'Content-Type: application/json' \
  -d '{"columns":[{"name":"SBP","description":"Systolic blood pressure (mmHg)"}]}'

# one request, restricted to a domain; the response echoes what it used
curl -s localhost:8000/match -H 'Content-Type: application/json' \
  -d '{"columns":[{"name":"height"}],"domains":["Measurement"]}'
```

`domains` is scoped to the request that carries it and is restored afterwards,
so a restricted call cannot leak its filter into the next caller's. An unknown
domain name is a 400 listing the valid ones, never a run that matches nothing.

`"shortlist": true` (config `exact_hit_shortlist`, off by default) is for
callers that show alternatives. A Stage 1 hit normally *is* the shortlist —
"Native Hawaiian" comes back alone, without "Native Hawaiian or Other Pacific
Islander". With the flag, Stages 2-6 still run after the hit, only to fill
`candidates` behind it: the selected concept, status, confidence and
`decided_at_stage` are exactly what they are without it, so both benchmarks
are unchanged (measured: gold set 92.0 / 97.8 / 96.0, user mappings 35 exact /
69 agreed / 68 top-5 / 43 unmapped, identical to the run without the change).
It costs the Stage 2-6 time on columns that would have exited at Stage 1.
Scoped to the request like `domains`; anything but a JSON boolean is a 400.

The listen address comes from `SERVICE_HOST` / `SERVICE_PORT` (default
`0.0.0.0:8000`); the database comes from the same `POSTGRES_*` variables as
everything else here. The matcher — connection pool, IDF index, embedding model
— is built once in the background at startup and shared by every request, and
rebuilt lazily if the database was not reachable then, so the service can start
before the vocabulary is ready.

This is what `../ETL_auto_designer` uses: its Concepts step has a **Load
concepts** button that posts every source column here and fills in the answers.
That stack's `conceptmatcher` service builds from this directory and shares the
`vocab-pipeline:latest` tag, so only one of the two ever builds the image, and
it bind-mounts `pipeline/` and `data/` from here just as the `pipeline` service
above does — so edits made here apply there too.

One difference from every other verb: `serve` is long-running, so it imports the
code once at startup. An edit that a CLI invocation would pick up immediately
needs a restart here (`docker compose restart conceptmatcher` in that stack, or
Ctrl-C and re-run). A stale server is otherwise silent — it keeps answering,
with the old ranking.

Written against the standard library on purpose — adding a web framework to
`requirements.txt` would invalidate the pip layer and re-download the whole
~2GB torch install on the next build, to save perhaps thirty lines.

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

Copy `pipeline.example.json` to `data/pipeline.json` and edit — that is the
path the container's `PIPELINE_CONFIG` points at. (Running from a host venv
instead, the default is `./pipeline.json`.) Precedence: `pipeline/config.py`
defaults < that file < the `pipeline.scoring_weight` table < CLI flags.

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
./pipeline.sh python benchmarks/compare_models.py    # end to end
./pipeline.sh python benchmarks/stage5_rerank.py     # the model in isolation
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
| none (Stage 5 off) | 90.0% | 95.6% | 40.0% | 243 |
| all-MiniLM-L6-v2 | 90.0% | 95.6% | 40.0% | 1579 |
| pubmedbert-base-embeddings | 90.0% | 95.6% | 40.0% | 529 |
| MedEmbed-small-v0.1 | 88.0% | 93.3% | 40.0% | 382 |

(Re-measured after the description-only interpretation was added; before it,
every row read 93.3% literal / 60.0% semantic at the same 90.0% overall. The
ms/col figures include per-process model load, so they are not comparable
across rows measured in different runs.)

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
   Stage 5 weight from 0.0 to 0.5 leaves end-to-end accuracy flat and
   degrades it at 0.5.

So Stage 5 earns its place only where the lexical stages are weak. If your
inputs are free-text clinical descriptions rather than column names, re-measure
before assuming any of this transfers — that is what the benchmarks are for.

Switching model is one config key plus a cache rebuild, since cached vectors
are keyed by model, dimension and document prefix:

```bash
# data/pipeline.json
{ "embedding_model": "abhinand/MedEmbed-small-v0.1" }

./pipeline.sh refresh --full
```

`EMBEDDING_MODEL_PRESETS` in `pipeline/config.py` carries the instruction
prefix each model needs. BGE- and E5-family models (which most medical
embedding models are built on) are trained with a prefix on the *query* side
only and lose accuracy without it; `embedding_query_prefix` and
`embedding_document_prefix` handle that asymmetry.

## Testing

```bash
./pipeline.sh test -v
```

71 tests. The Stage 0-6 tests are pure and run anywhere; the integration tests
need the loaded database and skip themselves cleanly when it is unreachable.

## Searching the description on its own

A column name is an identifier, not prose. `DOC_PAT_HAIR` contributes the
tokens `doc` and `pat`, which match no concept and dilute every score, while
its description — "Hair sample has been collected" — says the whole thing.
So when a column has a real description, Stage 0 emits **two**
interpretations: `name + description`, and the description alone. Stage 6
merges them per concept and keeps whichever scored higher, which is the same
machinery that already handles ambiguous abbreviations.

The second interpretation is only built when it can change something: the
description has to be at least four words (not a `Result value` stub, where
the name is the only signal) and the name has to contribute a word the
description does not already contain.

`match` prints both under `searched as`, so it is visible which query found
the winner:

```
  searched as   :
    [primary] 'doc pat hair sample collected'
    [description/primary] 'hair sample collected'
```

**This is a trade, not a free win.** Measured on both benchmarks:

| | name+description only | + description-only |
|---|---|---|
| gold set, literal (45 cases) | 93.3% | **95.6%** |
| gold set, semantic (5 cases) | **60.0%** | 40.0% |
| gold set, overall | 90.0% | 90.0% |
| user mappings, exact (117) | 28.2% | **29.1%** |
| user mappings, agreed on meaning | 57.3% | **59.0%** |
| user mappings, in top-5 | 45.3% | **47.9%** |
| user mappings, unmapped | 46 | **44** |
| ms/column | 459 | 834 |

(Both columns predate the keyword-dump filter and the equivalence table; see
the cumulative table under "Measured behaviour" for current numbers.)

It fixes columns whose *name* is noise (`spo2_pct` now finds oxygen
saturation) and breaks columns whose *description* is noise — `clotting_time`
/ "How long blood takes to clot" now ranks the morphologic "Blood clot" over
prothrombin time, because the name held the only precise term. On the gold set
that is one case each way; on the 206 real curated columns everything moved in
the right direction, which is why it is on by default.

Set `"description_only_variant": false` in `data/pipeline.json` to search
name + description only. That also halves the per-column cost, since most
columns otherwise run two interpretations through Stages 2-5.

## Synonyms that are not synonyms

41% of LOINC's synonyms (98,462 rows) are not alternative names but LOINC's
own search-keyword dump:

```
AOEObservation; Coll; Collect; Collected; Drugs; Nominal; Point in time;
Random; Sample timing related to dose; Spec; Typ
```

Indexed as searchable text, that lets a concept match any query containing any
fragment in the bag. For the column "Hair sample has been collected" it made
"Sample collection timing related to drug dose" the runner-up: Stage 4 found
the literal words `sample` and `collected` in the dump and scored it 73.3,
against a trigram similarity on the actual concept name of 0.32.

`public.pipeline_is_keyword_dump()` excludes them from `concept_search` and
from Stage 4's synonym probe. Two guards keep real synonyms:

- **Split on `;` followed by whitespace, never a bare `;`.** Genomic and
  cytogenetic names use a bare semicolon as syntax — `t(11;22)(q24;q12.2)`,
  `NM_005228.5:c.[2239_2256del;2258C>T]`. Splitting on it would have gutted
  1,611 OMOP Genomic synonyms.
- **Require short segments** (5+ of them, averaging ≤3 words). HCPCS separates
  long prose clauses with `"; "`; a keyword dump averages one or two words.

## Equivalent words

A column says `hair sample collected`; OMOP files the concept as "Collection
of hair **specimen**". Both sides stem, but `sampl` and `specimen` are
different stems, so the concept could not satisfy any rung requiring every
term — it fell to the loosest rung and was capped there.

[`resources/equivalents.json`](pipeline/resources/equivalents.json) holds
groups of interchangeable words, expanded on the **query** side only: Stage 2
asks for `hair & (sample|specimen) & collect`. The index is untouched, so the
table can be edited without a rebuild, and 3.5M concepts do not each become
matchable by more queries. `equivalents_local.json` adds site-specific groups.

The rules for adding a group are strict, because a wrong entry silently widens
every future search. Words must be substitutable in a concept *name*:
`kidney`/`renal` qualifies, `blood`/`serum` does not — "blood glucose" and
"serum glucose" are different LOINC concepts. No hypernyms, and no groups for
inflection, which stemming already handles.

Coverage is counted one slot per query **word**, with an equivalence group
sharing a slot. Collapsing a protected phrase into a single slot instead
reweights the query badly: for `egfr` / "Estimated glomerular filtration rate"
it made the phrase's four words worth as much as the single word `receptor`,
and the wrong expansion of the acronym overtook the right concept.

## Misspelt input

A misspelt word breaks every lexical stage at once. When it is the whole input
("Certoluzimab") Stage 2 finds nothing and the column is unmapped with no
candidates. When the rest of the input still matches ("Serum bolirubin level")
it is worse: the typo is silently ignored and the answer is whichever
"Serum ... level" concept ranks highest — Serum phenytoin level, in practice.

Stage 0 therefore spell-checks the input with
[SymSpell](https://github.com/mammothb/symspellpy) against a dictionary built
from the vocabulary itself: `pipeline.word_freq`, every word of every concept
name and English synonym with its frequency (`sql/14_pipeline_spelling.sql`,
built by `./setup_pipeline.sh`). A general English dictionary would not do —
it knows neither `certolizumab` nor `bilirubin`, and "corrects" what it does
not know. Code is in [`pipeline/spelling.py`](pipeline/spelling.py).

The rules, each one there because the simpler version failed:

- **A correction adds an interpretation; it never replaces the input.** The
  text as typed stays the primary interpretation and Stage 6 keeps the better
  concept across them, so a wrong correction costs a search pass, not the
  answer. `PASDAS` is "corrected" to `pandas` — harmlessly.
- **Words under 5 letters are never touched**, one edit is allowed up to 7
  letters and two from 8. Shorter words reach too many real words.
- **Known words are trusted — unless rare and next to a common one.** The
  vocabulary has typos of its own (`creatnine` ×1, `posphate` ×8), so a word
  seen at most 10 times that is one edit from a word 100× more frequent is
  corrected anyway.
- **Corrections never reach Stage 1, and never auto-accept.** A corrected
  interpretation is spelled exactly like the concept it was corrected towards,
  so every stage scores it 100. `Systelic` → `systemic` (one edit, and more
  frequent than `systolic`) auto-accepted "Systemic blood pressure" until a
  winner found only through a correction was capped at *review*, with a
  validation flag naming the correction.
- **Letter runs, not tokens:** `C-reactuve` is one token, `reactuve` the word.

Building the index costs ~7 s and ~450 MB, once per process, and only when an
input word is actually unknown or suspiciously rare — known words are answered
by a primary-key lookup, so a one-off `match` on clean text pays nothing.
After that, a correction costs well under a millisecond. Without `symspellpy`
installed the checker reports itself unavailable and corrects nothing.
`spelling_correction: false` in `pipeline.json` switches it off.

Measured by `benchmarks/compare_typos.py`: the 45 literal gold-set columns plus
35 drug names (`benchmarks/drug_set.csv`), each misspelt six ways in the word
that matters (`benchmarks/make_typo_set.py` → `typo_set.csv`). Description
only, so a column name cannot rescue a typo; all 80 clean descriptions are
right at top-1, so every miss is the typo's.

| 480 misspellings | before | trigram fallback on word_similarity | + spelling correction |
|---|---|---|---|
| right at top-1 | 60.0% | 62.1% | **97.9%** |
| drugs (210) | 85.2% | 90.0% | **100.0%** |
| labs (270) | 40.4% | 40.4% | **96.3%** |
| right *and* not unmapped | 19.2% | 17.3% | **97.1%** |
| two edits in one word (80) | 42.5% | 43.8% | **90.0%** |
| wrong concept auto-accepted | 0 | 0 | **0** |

Gold set and user mappings are unchanged by it (92.0 / 97.8 / 96.0; 35 exact,
69 agreed, 68 top-5, 43 unmapped), and so is steady-state latency.

What is left: two edits in a word under 8 letters (`soodum`, `glyocse`),
words under 5 letters (`Whie`), and a tie the frequency tiebreak gets wrong
(`Systelic` → `systemic`, held at review rather than accepted).

## Variables and values

The single largest remaining source of wrong answers is that OMOP contains
both the concept naming a **variable** and many concepts naming *statements
about* that variable, and the pipeline has no reliable way to prefer the
former:

| column | wanted | pipeline picks |
|---|---|---|
| `height` | `3036277 Body height` (Measurement) | `4203099 Normal height` (Clinical Finding) |
| "What is your profession?" | `36203487 Occupation [Type]` | `4076340 Employed` (Clinical Finding) |
| "Weight" | `4099154 Body weight` | fixed — see `never_variable_classes` |

Two mechanisms in the code address parts of this, and one measured attempt is
recorded here as a dead end:

**Stopwords are asymmetric.** `Preprocessor.concept_tokens` strips the
`generic`, `metadata` and `structural` buckets from a concept name but keeps
`instrument_boilerplate`. Those words — `above`, `below`, `average`,
`general` — are survey scaffolding in a column description and the entire
meaning in a concept name. Stripping them from both sides made

```
Height above average       -> {height}
General finding of height  -> {height}
Body height                -> {body, height}
```

so three concepts scored an identical, perfect Jaccard 1.0 against the column
`height` while the correct concept scored *lower* for carrying the extra word
`body`. A finding about the variable outranked the variable, structurally.

**`specificity_floor` is a measured dead end, kept as a config knob and
disabled by default.** Stage 2 can discount a candidate in proportion to how
much of its name the query never asked for — the mirror of `coverage`. It
does surface the variable-shaped concept (`Occupation [Type]` moves from rank
53 to 7), but at 0.70 it costs 2.2pp of gold-set literal accuracy and 4pp of
top-5, measured twice, before and after the stopword fix above.

The reason it cannot work as a word count: there are two kinds of extra word,
and this measure cannot tell them apart. *Specifying* words narrow the same
measurement — "Glucose **[Mass/volume] in Serum or Plasma**" — and penalising
them is what breaks the gold set. *Qualifying* words turn the variable into an
assertion about it — "**Normal** height", "Height **above average**" — and
penalising those is exactly right. The distinction is not in the name; it is
in `concept_class_id` and `domain_id`, which the database already carries.
That is where a real fix belongs.

**What exists today is `--domain`**, which does not infer the distinction but
lets the caller state it: `--domain Measurement` removes `Normal height`
(Observation / Clinical Finding) from contention entirely and leaves `Body
height` in the top two. It is a hard filter, described under "Restricting the
search to a domain" above. It is the right tool when a whole batch of columns
shares a domain — a study's lab panel, a questionnaire — and no help at all for
a mixed schema, where the domain is the thing that has to be inferred per
column. That inference is still the open problem.

## Measured behaviour

Cumulative effect of the four changes above, measured after each one. The
gold set is 50 lab/vitals columns graded by name rule; "user mappings" is 206
real curated columns from a rheumatology study, graded by concept_id over the
117 whose chosen concept exists in this standard-only database.

| | baseline | + description-only | + keyword-dump filter | + equivalents | + IDF stopword fix | **shipped** |
|---|---|---|---|---|---|---|
| gold literal (45) | 93.3% | 95.6% | **97.8%** | 97.8% | 97.8% | **97.8%** |
| gold semantic (5) | 60.0% | 40.0% | 40.0% | 40.0% | 40.0% | 40.0% |
| gold overall | 90.0% | 90.0% | **92.0%** | 92.0% | 92.0% | **92.0%** |
| gold top-5 | 94.0% | 92.0% | 94.0% | 94.0% | 94.0% | **96.0%** |
| user exact | 33 | 34 | **36** | 36 | 36 | **36** |
| user agreed on meaning | 67 | 69 | **71** | 71 | 69 | **71** |
| user in top-5 | 53 | 56 | 67 | 67 | 67 | **68** |
| user unmapped | 46 | 44 | **43** | 43 | 44 | 44 |
| ms/column | 459 | 834 | 749 | 784 | 683 | 700 |

The final column adds three fixes, all prompted by inspecting individual
wrong answers rather than by the benchmarks:

- **Stage 4 no longer eliminates on its own score.** It kept 10 of 15 ranked
  by fuzzy similarity alone, so a string-comparison stage could veto a
  candidate that Stage 2 and Stage 3 had both scored 100 — which is what
  happened to `36203487 Occupation [Type]` for the column "What is your
  profession?". It now cuts on the weighted mean of the evidence gathered so
  far (`scoring.provisional_score`), the way Stage 5 already behaves. Gold
  top-5 94% → 96%.
- **Stage 1 hard-filters `never_variable_classes`.** An exact name match could
  auto-accept at confidence 100 a class the config had already marked as never
  an ETL target, because a Stage 1 hit exits before Stage 6's penalty runs.
  The column "Weight" resolved to SNOMED 37111521 `Weight`
  (Observation / **Qualifier Value**) instead of `Body weight` (Measurement).
- **Instrument boilerplate is stripped from queries but kept in concept
  names** (see below).

Two honest readings of that table:

- **The keyword-dump filter is the biggest single win**, and most of it is
  recall: +11 columns gained their correct concept in the top 5. Junk synonyms
  were pushing real candidates out of the shortlist across the whole set, not
  just on the one column that exposed the bug.
- **The equivalence table changed nothing measurable here.** Only 16 of the
  206 columns contain any word in it, and none is a `sample`/`specimen` case.
  It demonstrably fixes that class of column — "Hair sample has been
  collected" goes from `unmapped 65.5` to `review 70.1`, reaching rung 0 with
  an FTS score of 100 instead of 87.5 — but neither benchmark covers it. It is
  kept because it is neutral on both and costs ~5% latency, not because
  either benchmark endorses it. A study with no specimen or anatomy language
  can set `equivalents_local.json` aside and lose nothing.
- **The IDF stopword fix costs 2 agreed-on-meaning columns and is kept
  anyway.** `ts_lexize('english_stem', 'your')` returns an empty array — the
  dictionary's way of saying "stopword" — where an unknown word returns
  `{word}`. Joining on `lexemes[1]` turned that into NULL, `COALESCE(doc_freq,
  1)` made it df=1, and the word scored `log(1 + 3,518,557/2)` = **14.38**, the
  maximum possible weight. For "What is your profession?" Stage 3 weighted
  `your` above `profession`. Every stopword reaching Stage 3 was scored as the
  rarest token in the corpus. The two columns lost were riding on that: a
  questionnaire item and a questionnaire concept share those pronouns, so the
  inflated weight was accidentally rewarding a real match. Correct behaviour
  arrived at for the wrong reason is still a bug.

**A change that measured worse and was reverted:** adding personal and
possessive pronouns to the Stage 0 stopword list. The argument looked
identical to the question-word case — Postgres discards them anyway, so
keeping them can only add noise. It is true for Stage 2 and false for Stages 3
and 4: a questionnaire column matches a questionnaire concept partly *on* those
pronouns, and question shape is real signal when both sides are questions. It
cost 2 agreed-on-meaning and 2 more unmapped columns. Isolating it needed one
benchmark run per change rather than one for the batch.

| variant | exact | agreed | top-5 | unmapped |
|---|---|---|---|---|
| all four changes | 36 | 67 | 66 | 46 |
| equivalence folding off | 36 | 68 | 66 | 47 |
| **pronoun stopwords reverted (shipped)** | **36** | **69** | **67** | **44** |

**"Rank concepts with extra unrelated words lower" has now failed four times,
measured, in four different forms.** It is the most natural-sounding idea about
this pipeline and it does not work. Recorded in full so it is not attempted a
fifth time:

1. `specificity_floor` — Stage 2 discounts a candidate by how much of its name
   the query never asked for. Cost 2.2pp gold literal, 4pp gold top-5.
2. Symmetric F1 in Stage 4 — replace the recall-only token score with the
   harmonic mean of both directions. Checked offline before implementing: it
   orders the smoking query correctly *and* scores the **correct** gold answers
   at F1 37-51 against a recall of 100.
3. `verbosity_floor` — damp a candidate whose name is far longer than the
   query, **gated on query length** so that short lab queries are exempt. Built
   and measured at 0.75 and 0.55; worse or equal on every hard metric.
4. Length as a Stage 2 `ORDER BY` tie-break — already present, and almost never
   fires because `ts_rank_cd` values rarely tie exactly.

The reason all four fail is one measurement. For the column "How many cigars or
pipes do you smoke per day?" the *wrong* 27-word NHANES concept about smelling
other people's smoke at work scores **precision 49%**; for `glucose_mgdl` the
*correct* "Glucose [Mass/volume] in Serum or Plasma" scores **precision 23%**.
The correct answers are less precise than the wrong ones, so no monotone
function of length or precision can separate them.

Gating on query length does not rescue it either: 11 of the 50 gold cases have
four or more query tokens (`Gamma glutamyl transferase serum`,
`Absolute neutrophil count`, `Oxygen saturation by pulse oximetry`, …), so the
gate exempts far less than it appears to.

| `verbosity_floor` | gold overall | gold literal | gold top-5 | user exact | user agreed | user unmapped |
|---|---|---|---|---|---|---|
| **1.0 (off, shipped)** | **92.0%** | **97.8%** | **96.0%** | **35** | 69 | **43** |
| 0.75 | 90.0% | 95.6% | 96.0% | 34 | 69 | 43 |
| 0.55 | 90.0% | 95.6% | 94.0% | 34 | 70 | 44 |

The distinction is semantic, not structural: in the gold cases the extra words
are *attributes of the measurement*; in the smoking case they introduce a
*different subject* (`job`, `employer`, `occupation`, `smell`, `other
peoples`). Stage 5 is the only signal that gets it right — it ranked the
correct concept 70.1 against the wrong one's 56.6 — and it is outvoted because
Stage 2's rung 0 and Stage 4's token score are **the same evidence counted
twice** ("the concept contains every query term"), holding 0.35 + 0.20 of the
weight between them. De-correlating those two signals, or using
`concept_class_id` and `domain_id`, is where the next attempt should go — not
another length penalty.

**What `--domain` measures as.** It is off by default and both benchmarks are
unchanged with it off, to the number. Turned on, it does not move either
benchmark's accuracy either — and that is the useful finding, because it says
where the restriction does and does not belong.

| | gold overall | gold literal | gold top-5 | ms/col |
|---|---|---|---|---|
| **no restriction (default)** | 92.0% | 97.8% | 96.0% | 208 |
| `--domain Measurement` | 92.0% | 97.8% | 96.0% | **130** |
| `--domain Measurement,Observation` | 92.0% | 97.8% | 96.0% | **129** |

| 206 curated columns | exact | agreed | top-5 | unmapped |
|---|---|---|---|---|
| **no restriction (default)** | **35** | 69 | **68** | 43 |
| `--domain Measurement,Observation` | 35 | **70** | 67 | **41** |

Three things follow:

- **It is free when it is right.** The gold set's answers are Measurement
  concepts already, so restricting to Measurement changes no answer — and cuts
  **38% of the latency** (208 → 130 ms/column), because Stage 2's candidate
  pool is smaller. Measured warm, three runs, reproducible to ±1 ms.
- **It does not fix what is actually wrong on the curated set.** +1 agreed, −1
  top-5, −2 unmapped is noise. 94 of the 113 reachable human answers there are
  already Observation concepts, and the pipeline's wrong answers are wrong
  *within* the right domain — another LOINC instrument's copy of the same
  question. The domain was never what was going wrong on that set.
- **Where it does work is the single column.** `height` unrestricted returns
  `4203099 Normal height` (Observation / Clinical Finding); with `--domain
  Measurement` the top two are `37174447 Patient height` and `3036277 Body
  height`. That is the "variables and values" defect, and this is the only tool
  here that touches it — by letting the caller supply the answer rather than
  inferring it.

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
