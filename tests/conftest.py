"""Shared test fixtures.

These tests run entirely offline: ``FakeDatabase`` mimics the read-only
``Database`` interface used by the tool functions, returning canned rows based
on a substring match against the SQL text. This lets us verify each tool's
output shape and serialization without a live Postgres/Supabase instance.
"""

from __future__ import annotations

from typing import Any, Iterable

import pytest


class FakeDatabase:
    """A stand-in for :class:`pet_grooming_mcp.database.Database`.

    Register responses with ``when(substring, rows)``. Each :meth:`fetch` returns
    the rows of the first rule whose substring appears in the query.
    """

    def __init__(self) -> None:
        self._rules: list[tuple[str, list[dict[str, Any]]]] = []
        self.queries: list[str] = []

    def when(self, substring: str, rows: Iterable[dict[str, Any]]) -> "FakeDatabase":
        self._rules.append((substring, list(rows)))
        return self

    async def fetch(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        self.queries.append(query)
        for substring, rows in self._rules:
            if substring in query:
                return rows
        return []

    async def fetchrow(self, query: str, params: Any = None) -> dict[str, Any] | None:
        rows = await self.fetch(query, params)
        return rows[0] if rows else None

    def clamp_limit(self, limit: int | None, default: int = 25) -> int:
        if limit is None:
            limit = default
        return max(1, min(int(limit), 500))


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()
