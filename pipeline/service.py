"""An HTTP front door for the matching pipeline.

    python -m pipeline.service              # listens on 0.0.0.0:8000
    ./pipeline.sh serve                     # the same, in the image

Three endpoints, all JSON:

    GET  /health   readiness, plus what the pipeline actually has loaded
    GET  /         alias for /health, so a container healthcheck can hit `/`
    POST /match    {"columns": [{"name", "description"?, "table"?}, ...],
                    "domains"?: ["Measurement", ...], "candidates"?: 5,
                    "shortlist"?: true,
                    "source_system"?, "note"?}

``domains`` restricts that request to those OMOP domains and nothing else; an
unknown domain name is a 400 rather than a run that quietly matches nothing.
Omit the key to use whatever the process was configured with; pass ``[]`` to
lift the restriction for one request.

Written against the standard library on purpose. The image already carries CPU
torch and sentence-transformers and is ~2GB; adding a web framework to
requirements.txt would invalidate the pip layer and force that whole install to
be downloaded again on the next build. The API surface here is two handlers, so
the framework would buy nothing.

The service is a thin shell around Stage 8 — `BatchMatcher.run` is what does
the work, exactly as `pipeline.cli batch` calls it, so a column mapped through
this endpoint takes the same path (and lands in the same log tables) as one
mapped from the command line.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .config import PipelineConfig
from .models import MatchResult
from .stage8_batch import BatchMatcher

log = logging.getLogger("pipeline.service")

# A column list arrives as one JSON body; these bound what a single request can
# ask for so a malformed or hostile caller cannot exhaust memory or the pool.
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_COLUMNS = 2000
DEFAULT_CANDIDATES = 5
MAX_CANDIDATES = 25


class MatchService:
    """One shared pipeline, built on first use and reused for every request.

    ``ConceptMatcher`` is thread-safe by design (Stage 8 already drives it from
    a thread pool), so the expensive parts — the connection pool, the IDF index,
    the embedding model — are opened once for the life of the process rather
    than per request.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._build_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._batch: BatchMatcher | None = None

    # ── lifecycle ───────────────────────────────────────────────────────
    def batcher(self) -> BatchMatcher:
        """The shared Stage 8 matcher, constructed on first use.

        Built lazily rather than at startup so the service still comes up when
        the vocabulary database is not reachable yet — /health then reports the
        failure, and the next request retries instead of the container being
        stuck in a restart loop.
        """
        if self._batch is None:
            with self._build_lock:
                if self._batch is None:
                    batch = BatchMatcher(config=self.config)
                    # Pay the ~10s model load here, once, instead of charging it
                    # to whichever column reaches Stage 5 first.
                    if batch.matcher.stage5.enabled:
                        batch.matcher.stage5.model.encode(["warmup"])
                    self._batch = batch
        return self._batch

    def close(self) -> None:
        if self._batch is not None:
            self._batch.close()
            self._batch = None

    # ── endpoints ───────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        batch = self.batcher()
        db = batch.matcher.db
        problems = db.check_ready()
        stage5 = batch.matcher.stage5
        return {
            "ready": not problems,
            "problems": list(problems),
            "vocabulary_version": db.vocabulary_version(),
            "concepts": db.fetch_value("SELECT count(*) FROM pipeline.concept_search"),
            "embeddings": bool(stage5.enabled),
            "embedding_model": self.config.embedding_model if stage5.enabled else None,
            "auto_accept_threshold": self.config.auto_accept_threshold,
            "review_threshold": self.config.review_threshold,
            "restrict_domains": list(self.config.restrict_domains),
            "domains": [row["domain_id"] for row in db.domain_counts()],
        }

    def match(
        self,
        columns: list[dict[str, Any]],
        source_system: str | None = None,
        note: str | None = None,
        candidate_limit: int = DEFAULT_CANDIDATES,
        domains: list[str] | None = None,
        shortlist: bool | None = None,
    ) -> dict[str, Any]:
        """Map every column. ``domains`` restricts this request's candidates.

        ``None`` keeps the process-wide restriction; a list (including an empty
        one) replaces it for the duration of this request. Raises ValueError on
        a domain name the vocabulary does not define.

        ``shortlist`` likewise overrides ``config.exact_hit_shortlist`` for this
        request only: True keeps building the candidate list behind a Stage 1
        hit, for callers that show alternatives.
        """
        batch = self.batcher()
        # Resolved before the lock is taken, so a bad domain name costs the
        # caller a 400 and costs a concurrent request nothing.
        restriction = (None if domains is None
                       else tuple(batch.matcher.db.resolve_domains(domains)))

        # Serialized: Stage 8 already parallelizes across `batch_workers`, so
        # letting two batches run at once would only oversubscribe the same
        # connection pool and interleave their run ids. The restriction rides on
        # the shared config, which is safe only because of this lock — and it is
        # restored afterwards so one request's filter cannot leak into the next.
        with self._run_lock:
            default = self.config.restrict_domains
            default_shortlist = self.config.exact_hit_shortlist
            if restriction is not None:
                self.config.restrict_domains = restriction
            if shortlist is not None:
                self.config.exact_hit_shortlist = shortlist
            try:
                run_id, results = batch.run(columns, source_system, note)
            finally:
                self.config.restrict_domains = default
                self.config.exact_hit_shortlist = default_shortlist
        return {
            "run_id": run_id,
            # Echoed so a caller can see which filter the run actually used,
            # after case-correction ("meas value" -> "Meas Value").
            "domains": list(restriction if restriction is not None else default),
            "results": [_result_json(r, candidate_limit) for r in results],
        }


def _candidate_json(candidate: Any) -> dict[str, Any]:
    return {
        "concept_id": candidate.concept_id,
        "concept_name": candidate.concept_name,
        "domain_id": candidate.domain_id,
        "vocabulary_id": candidate.vocabulary_id,
        "concept_class_id": candidate.concept_class_id,
        "concept_code": candidate.concept_code,
        "score": round(candidate.composite_score, 2),
    }


def _result_json(result: MatchResult, candidate_limit: int) -> dict[str, Any]:
    """One column's outcome, trimmed to what a caller needs to fill in a form.

    Deliberately narrower than ``MatchResult.as_dict`` — that carries the whole
    Stage 0 context and per-stage scores, which is the right payload for the log
    tables and far more than an HTTP client wants per column.
    """
    selected = result.selected
    return {
        "column_name": result.context.column_name,
        "column_description": result.context.column_description,
        # Echoed back so a caller whose column names repeat across tables can
        # still tie each result to the row it asked about.
        "source_table": result.context.source_table,
        "status": result.status,
        "confidence": round(result.confidence, 2),
        "concept_id": result.concept_id,
        "concept_name": selected.concept_name if selected else None,
        "domain_id": selected.domain_id if selected else None,
        "vocabulary_id": selected.vocabulary_id if selected else None,
        "concept_class_id": selected.concept_class_id if selected else None,
        "concept_code": selected.concept_code if selected else None,
        "decided_at_stage": result.decided_at_stage,
        "decision_reason": result.decision_reason,
        "ambiguous": result.ambiguous,
        "validation_flags": list(result.validation_flags),
        "elapsed_ms": round(result.elapsed_ms, 2),
        "error": result.error,
        "candidates": [_candidate_json(c) for c in result.candidates[:candidate_limit]],
    }


def parse_columns(payload: Any) -> list[dict[str, Any]]:
    """Validate the request body into what ``BatchMatcher.run`` expects.

    Accepts the same shapes ``read_columns`` does for a JSON file — objects with
    ``name``/``description``/``table``, or bare strings — so a caller can post
    the file it would otherwise have handed to `pipeline.cli batch`.
    """
    if isinstance(payload, dict):
        payload = payload.get("columns")
    if not isinstance(payload, list):
        raise ValueError("expected a JSON object with a 'columns' list")
    if len(payload) > MAX_COLUMNS:
        raise ValueError(f"too many columns: {len(payload)} > {MAX_COLUMNS}")

    columns: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            raise ValueError(f"columns[{index}] is neither a string nor an object")
        name = (item.get("name") or "").strip()
        description = (item.get("description") or "").strip() or None
        if not name and not description:
            # Stage 0 can work from a description alone, but not from nothing.
            raise ValueError(f"columns[{index}] needs a 'name' or a 'description'")
        columns.append({
            "name": name,
            "description": description,
            "table": (item.get("table") or "").strip() or None,
        })
    return columns


def parse_domains(payload: Any) -> list[str] | None:
    """The request's ``domains``, or None when the key is absent.

    Absent and empty are deliberately different: absent means "use the
    process's restriction", ``[]`` means "no restriction for this request". A
    bare string is accepted as a one-element list, because that is what a caller
    writes when they want a single domain.
    """
    if not isinstance(payload, dict) or "domains" not in payload:
        return None
    value = payload["domains"]
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("'domains' must be a string or a list of strings")
    return [v.strip() for v in value if v.strip()]


def parse_shortlist(payload: Any) -> bool | None:
    """The request's ``shortlist`` flag, or None when absent (keep the
    process's ``exact_hit_shortlist``). Only a real JSON boolean counts, so a
    stray ``"false"`` string cannot switch it on."""
    if not isinstance(payload, dict) or "shortlist" not in payload:
        return None
    value = payload["shortlist"]
    if not isinstance(value, bool):
        raise ValueError("'shortlist' must be true or false")
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = "omop-concept-matcher/1.0"
    protocol_version = "HTTP/1.1"

    # Set on the server instance in serve() below.
    service: MatchService

    # ── plumbing ────────────────────────────────────────────────────────
    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, default=str).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            # The caller hung up before we answered — a container healthcheck
            # that timed out during the first (model-loading) request, or a
            # cancelled browser request. Not worth a stack trace per occurrence.
            log.debug("client disconnected before the response was written")
            self.close_connection = True

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("empty request body")
        if length > MAX_BODY_BYTES:
            raise ValueError(f"request body too large: {length} > {MAX_BODY_BYTES}")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"body is not valid JSON: {exc}") from exc

    def log_message(self, fmt: str, *args: Any) -> None:
        # BaseHTTPRequestHandler writes to stderr unformatted; route it through
        # logging so container logs look like the rest of the pipeline's.
        log.info("%s - %s", self.address_string(), fmt % args)

    # ── routes ──────────────────────────────────────────────────────────
    def do_GET(self) -> None:      # noqa: N802 — BaseHTTPRequestHandler's name
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path not in ("/", "/health"):
            self._send(404, {"detail": f"no such endpoint: {path}"})
            return
        try:
            self._send(200, self.server.service.health())        # type: ignore[attr-defined]
        except Exception as exc:
            # Almost always "the vocabulary database is not up yet". 503 rather
            # than 500 so a caller can distinguish it from a bug and retry.
            log.warning("health check failed: %s", exc)
            self._send(503, {"ready": False, "detail": str(exc)})

    def do_POST(self) -> None:     # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/match":
            self._send(404, {"detail": f"no such endpoint: {path}"})
            return

        try:
            payload = self._read_body()
            columns = parse_columns(payload)
            domains = parse_domains(payload)
            shortlist = parse_shortlist(payload)
        except ValueError as exc:
            self._send(400, {"detail": str(exc)})
            return

        options = payload if isinstance(payload, dict) else {}
        candidate_limit = min(MAX_CANDIDATES, max(0, int(
            options.get("candidates") or DEFAULT_CANDIDATES)))

        if not columns:
            self._send(200, {"run_id": 0, "domains": domains or [], "results": []})
            return

        try:
            body = self.server.service.match(                     # type: ignore[attr-defined]
                columns,
                source_system=options.get("source_system") or None,
                note=options.get("note") or None,
                candidate_limit=candidate_limit,
                domains=domains,
                shortlist=shortlist,
            )
        except ValueError as exc:
            # An unresolvable domain name is the caller's mistake, not a
            # pipeline failure — 400, with the list of valid names in the text.
            self._send(400, {"detail": str(exc)})
            return
        except Exception as exc:
            log.exception("match failed")
            self._send(503, {"detail": f"match failed: {exc}"})
            return
        self._send(200, body)


def serve(host: str = "0.0.0.0", port: int = 8000,
          config: PipelineConfig | None = None) -> None:
    service = MatchService(config or PipelineConfig.load())
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.service = service                                      # type: ignore[attr-defined]

    # Build the matcher off the request path, so the connection pool and the
    # ~10s embedding model load are already done by the time the first caller
    # arrives — otherwise whichever request lands first (usually a container
    # healthcheck with a short timeout) is the one that pays for it.
    def warmup() -> None:
        try:
            service.batcher()
            log.info("matcher ready")
        except Exception as exc:
            # Almost always "the vocabulary database is not up yet". Requests
            # rebuild lazily, so this is a slow start, not a failure.
            log.warning("warmup deferred: %s", exc)

    threading.Thread(target=warmup, name="warmup", daemon=True).start()

    log.info("listening on %s:%s (vocabulary at %s:%s/%s)",
             host, port, service.config.host, service.config.port,
             service.config.dbname)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("PIPELINE_LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    serve(
        host=os.environ.get("SERVICE_HOST", "0.0.0.0"),
        port=int(os.environ.get("SERVICE_PORT", "8000")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
