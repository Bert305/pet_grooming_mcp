"""Read-only async database access for the analytics tools.

All access goes through a small connection pool whose connections are pinned to
READ ONLY transactions with a bounded statement timeout. Combined with the fact
that every tool issues only fixed, parameterised queries (no user-supplied SQL),
this makes it very hard for a client to mutate or overload the database.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import Config


class Database:
    """A thin, read-only wrapper around an async psycopg connection pool."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._pool: AsyncConnectionPool | None = None

    async def connect(self) -> None:
        """Open the connection pool and verify connectivity."""
        if self._pool is not None:
            return

        self._pool = AsyncConnectionPool(
            conninfo=self._config.database_url,
            min_size=self._config.pool_min_size,
            max_size=self._config.pool_max_size,
            open=False,
            configure=self._configure,
            # Belt-and-suspenders: ask the server for read-only by default too.
            kwargs={"options": "-c default_transaction_read_only=on"},
        )
        await self._pool.open(wait=True)

    async def _configure(self, conn: Any) -> None:
        """Pin every pooled connection to read-only with a statement timeout."""
        await conn.set_autocommit(True)
        await conn.set_read_only(True)
        async with conn.cursor() as cur:
            # `SET` does not accept bind parameters, so use set_config(), which
            # does. The value is server-validated config, never client input.
            await cur.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (str(self._config.statement_timeout_ms),),
            )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("Database pool is not open; call connect() first.")
        return self._pool

    async def fetch(
        self,
        query: str,
        params: Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a query and return all rows as dictionaries."""
        pool = self._require_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                return await cur.fetchall()

    async def fetchrow(
        self,
        query: str,
        params: Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> dict[str, Any] | None:
        """Run a query and return the first row (or ``None``)."""
        rows = await self.fetch(query, params)
        return rows[0] if rows else None

    def clamp_limit(self, limit: int | None, default: int = 25) -> int:
        """Clamp a user-supplied row limit into a safe range."""
        if limit is None:
            limit = default
        return max(1, min(int(limit), self._config.max_row_limit))
