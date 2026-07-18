"""Helpers for turning raw database rows into JSON-serializable structures.

Postgres numeric/timestamp columns come back as :class:`~decimal.Decimal`,
:class:`~datetime.datetime`, and :class:`~datetime.date` objects, none of which
are JSON-serializable by default. ``jsonable`` normalises those into floats and
ISO-8601 strings so tool results can be returned to the MCP client as-is.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def jsonable(value: Any) -> Any:
    """Recursively convert database values into JSON-safe Python types."""
    if isinstance(value, Decimal):
        # Preserve integers as ints, everything else as float.
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value
