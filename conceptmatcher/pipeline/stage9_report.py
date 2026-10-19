"""Stage 9 — reporting and monitoring.

Reads the Stage 7.4 log tables through the views defined in
``sql/11_pipeline_schema.sql`` and renders them as plain text tables.

Nothing here computes anything the database cannot: the aggregation lives in
SQL views so the same numbers are available to any BI tool pointed at this
database, and this module only formats them.
"""

from __future__ import annotations

from typing import Any, Sequence

from .db import Database


class Reporter:
    """Stage 9."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Step 9.1 ────────────────────────────────────────────────────────
    def mapping_statistics(self, run_id: int | None = None) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT * FROM pipeline.v_mapping_stats
            WHERE (%(run_id)s::bigint IS NULL OR run_id = %(run_id)s)
            ORDER BY run_id DESC
            """,
            {"run_id": run_id},
        )

    def domain_confidence(self, run_id: int | None = None) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT * FROM pipeline.v_domain_confidence
            WHERE (%(run_id)s::bigint IS NULL OR run_id = %(run_id)s)
            ORDER BY n DESC
            """,
            {"run_id": run_id},
        )

    def failure_reasons(self, run_id: int | None = None,
                        limit: int = 15) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT * FROM pipeline.v_failure_reasons
            WHERE (%(run_id)s::bigint IS NULL OR run_id = %(run_id)s)
            ORDER BY n DESC
            LIMIT %(limit)s
            """,
            {"run_id": run_id, "limit": limit},
        )

    # ── Step 9.2 ────────────────────────────────────────────────────────
    def quality_metrics(self, run_id: int | None = None) -> dict[str, Any]:
        """Precision and recall proxies, measured against human corrections.

        There is no ground truth to compare against in general, so the only
        honest signal available is the subset a human has since corrected:

        * *precision proxy* — of the auto-accepted mappings a human later
          revisited, how many were left alone.
        * *recall proxy* — the share of columns that got any mapping at all.

        Both are proxies, and both are only meaningful once
        ``pipeline.manual_mapping`` has entries; with none, precision is
        reported as NULL rather than as a perfect score.
        """
        row = self.db.fetch_one(
            """
            WITH reviewed AS (
                SELECT l.mapping_id,
                       l.selected_concept_id,
                       m.concept_id AS human_concept_id
                FROM pipeline.mapping_log l
                JOIN pipeline.manual_mapping m
                     ON m.lookup_key = public.pipeline_normalize(
                            l.column_name || ' ' || COALESCE(l.column_description, ''))
                    AND m.active
                WHERE l.status = 'auto_accept'
                  AND (%(run_id)s::bigint IS NULL OR l.run_id = %(run_id)s)
            ),
            totals AS (
                SELECT count(*) AS n,
                       count(*) FILTER (WHERE status <> 'unmapped') AS mapped
                FROM pipeline.mapping_log
                WHERE (%(run_id)s::bigint IS NULL OR run_id = %(run_id)s)
            )
            SELECT
                (SELECT n FROM totals)      AS total_columns,
                (SELECT mapped FROM totals) AS mapped_columns,
                round(100.0 * (SELECT mapped FROM totals)
                      / NULLIF((SELECT n FROM totals), 0), 1) AS recall_proxy_pct,
                (SELECT count(*) FROM reviewed) AS human_checked,
                CASE WHEN (SELECT count(*) FROM reviewed) = 0 THEN NULL
                     ELSE round(100.0 * (SELECT count(*) FROM reviewed
                                         WHERE selected_concept_id = human_concept_id)
                                / (SELECT count(*) FROM reviewed), 1)
                END AS precision_proxy_pct
            """,
            {"run_id": run_id},
        )
        return row or {}

    def unmapped_tokens(self, run_id: int | None = None,
                        limit: int = 20) -> list[dict[str, Any]]:
        """Step 9.2: which tokens keep appearing in columns we could not map.

        This is the dictionary-expansion worklist — a token near the top here
        is usually an abbreviation or phrase Stage 0 does not know.
        """
        return self.db.fetch_all(
            """
            SELECT * FROM pipeline.v_unmapped_tokens
            WHERE (%(run_id)s::bigint IS NULL OR run_id = %(run_id)s)
            ORDER BY n DESC, token
            LIMIT %(limit)s
            """,
            {"run_id": run_id, "limit": limit},
        )

    # ── Step 9.3 ────────────────────────────────────────────────────────
    def stage_performance(self, run_id: int | None = None) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT * FROM pipeline.v_stage_performance
            WHERE (%(run_id)s::bigint IS NULL OR run_id = %(run_id)s)
            ORDER BY total_ms DESC
            """,
            {"run_id": run_id},
        )

    def ambiguous_mappings(self, run_id: int | None = None,
                           limit: int = 40) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT * FROM pipeline.v_ambiguous_mappings
            WHERE (%(run_id)s::bigint IS NULL OR run_id = %(run_id)s)
            LIMIT %(limit)s
            """,
            {"run_id": run_id, "limit": limit},
        )

    # ────────────────────────────────────────────────────────────────────
    def render(self, run_id: int | None = None) -> str:
        """The whole Stage 9 report as text."""
        blocks = [
            ("Mapping statistics (Step 9.1)", self.mapping_statistics(run_id)),
            ("Confidence by domain (Step 9.1)", self.domain_confidence(run_id)),
            ("Reasons for review / unmapped (Step 9.1)", self.failure_reasons(run_id)),
            ("Quality metrics (Step 9.2)",
             [self.quality_metrics(run_id)] if self.quality_metrics(run_id) else []),
            ("Unmapped tokens — dictionary worklist (Step 9.2)",
             self.unmapped_tokens(run_id)),
            ("Stage performance (Step 9.3)", self.stage_performance(run_id)),
            ("Ambiguous mappings (Step 7.1)", self.ambiguous_mappings(run_id)),
        ]
        parts = []
        for title, rows in blocks:
            parts.append(f"\n{title}\n{'=' * len(title)}")
            parts.append(format_table(rows))
        return "\n".join(parts)


def format_table(rows: Sequence[dict[str, Any]], max_width: int = 60) -> str:
    """Render query rows as an aligned text table."""
    if not rows:
        return "  (no rows)"

    headers = list(rows[0].keys())
    cells = [
        [_truncate(row.get(h), max_width) for h in headers]
        for row in rows
    ]
    widths = [
        max(len(header), *(len(row[i]) for row in cells))
        for i, header in enumerate(headers)
    ]

    lines = [
        "  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
        "  " + "  ".join("-" * widths[i] for i in range(len(headers))),
    ]
    lines += [
        "  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        for row in cells
    ]
    return "\n".join(lines)


def _truncate(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
