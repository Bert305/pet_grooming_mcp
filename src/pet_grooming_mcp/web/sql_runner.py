"""Safe execution of ad-hoc, read-only SQL for the query maker and analyzer.

The database connection is already pinned to ``READ ONLY`` at the pool layer, so
the server *cannot* be made to mutate data. This module adds a second, cheap
line of defence at the application layer: it rejects anything that isn't a
single ``SELECT`` / ``WITH`` statement before the query ever reaches Postgres,
which gives the user a clear error instead of a driver-level failure.
"""

from __future__ import annotations

import re
from typing import Any

from ..database import Database
from ..models import jsonable

# Row ceiling for any ad-hoc query. Kept well under the analytics tools' own
# limits so a runaway "SELECT * FROM appointments" can't flood the browser.
DEFAULT_MAX_ROWS = 1000

# Statements that must never run through the ad-hoc paths. The read-only
# transaction blocks the destructive ones anyway; listing them lets us return a
# friendly message rather than a Postgres "cannot execute ... in a read-only
# transaction" error.
_FORBIDDEN = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "comment",
    "copy",
    "call",
    "merge",
    "vacuum",
    "analyze",
    "reindex",
    "refresh",
    "cluster",
    "lock",
    "listen",
    "notify",
    "prepare",
    "execute",
    "do",
    "set",
    "reset",
    "begin",
    "commit",
    "rollback",
    "savepoint",
)

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


class SqlValidationError(ValueError):
    """Raised when an ad-hoc query fails the read-only guard rails."""


def _strip_comments(sql: str) -> str:
    return _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", sql))


def validate(sql: str) -> str:
    """Validate and normalise an ad-hoc query, returning the cleaned SQL.

    Raises :class:`SqlValidationError` if the statement is empty, contains more
    than one statement, doesn't start with ``SELECT``/``WITH``, or mentions a
    forbidden (write / DDL / transaction-control) keyword.
    """
    if not sql or not sql.strip():
        raise SqlValidationError("Query is empty.")

    cleaned = _strip_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        raise SqlValidationError("Query is empty after removing comments.")

    # Reject multiple statements. After stripping a single trailing ';' above,
    # any remaining ';' means the user tried to chain statements.
    if ";" in cleaned:
        raise SqlValidationError("Only a single statement is allowed.")

    lowered = cleaned.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise SqlValidationError("Only SELECT (or WITH ... SELECT) queries are allowed.")

    # Whole-word match so columns like "created_at" don't trip the "create" rule.
    words = set(re.findall(r"[a-z_]+", lowered))
    banned = words.intersection(_FORBIDDEN)
    if banned:
        raise SqlValidationError(
            f"Disallowed keyword(s): {', '.join(sorted(banned))}. "
            "This endpoint is read-only."
        )
    return cleaned


async def run_query(
    db: Database, sql: str, max_rows: int = DEFAULT_MAX_ROWS
) -> dict[str, Any]:
    """Validate then execute an ad-hoc read-only query.

    Returns a JSON-safe dict with ``columns``, ``rows``, ``row_count``,
    ``truncated`` and the ``sql`` that actually ran.
    """
    cleaned = validate(sql)
    max_rows = max(1, min(int(max_rows), DEFAULT_MAX_ROWS))
    columns, rows, truncated = await db.fetch_capped(cleaned, max_rows=max_rows)
    return jsonable(
        {
            "sql": cleaned,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }
    )
