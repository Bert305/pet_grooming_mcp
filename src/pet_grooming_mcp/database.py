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
        # Hold onto config; the pool is created lazily in connect() so constructing
        # a Database never opens a network connection on its own.
        self._config = config
        self._pool: AsyncConnectionPool | None = None

    async def connect(self) -> None:
        """Open the connection pool and verify connectivity."""
        # Idempotent: if the pool already exists, connecting again is a no-op.
        if self._pool is not None:
            return

        # Build the pool but defer opening (open=False) so we can open explicitly
        # below and wait for it to be ready. `configure` runs _configure() on each
        # new connection; kwargs sets the server-side read-only default.
        self._pool = AsyncConnectionPool(
            conninfo=self._config.database_url,
            min_size=self._config.pool_min_size,
            max_size=self._config.pool_max_size,
            open=False,
            configure=self._configure,
            # Belt-and-suspenders: ask the server for read-only by default too.
            kwargs={"options": "-c default_transaction_read_only=on"},
        )
        # wait=True blocks until the minimum number of connections is established,
        # so a failure surfaces here rather than on the first query.
        await self._pool.open(wait=True)

    async def _configure(self, conn: Any) -> None:
        """Pin every pooled connection to read-only with a statement timeout."""
        # autocommit avoids leaving an open transaction between queries; read-only
        # blocks any writes at the connection level (the primary safety guarantee).
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
        # Guarded so calling close() when never connected (or twice) is harmless.
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> AsyncConnectionPool:
        # Internal accessor used by every query method: fail loudly with a clear
        # message if a caller forgot to connect() before running a query.
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
        # `async with pool.connection()` borrows a connection and returns it to the
        # pool on exit. dict_row makes each row a {column: value} dict; params are
        # passed to execute() so psycopg binds them safely (no string formatting).
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
        # Convenience wrapper over fetch() for single-row lookups; returns None
        # instead of raising when the query matches nothing.
        rows = await self.fetch(query, params)
        return rows[0] if rows else None

    async def fetch_capped(
        self,
        query: str,
        params: Mapping[str, Any] | Sequence[Any] | None = None,
        max_rows: int = 1000,
    ) -> tuple[list[str], list[dict[str, Any]], bool]:
        """Run a query and return at most ``max_rows`` rows.

        Returns ``(columns, rows, truncated)`` where ``columns`` preserves the
        SELECT order, ``rows`` are dictionaries, and ``truncated`` indicates that
        more rows were available but withheld. Used for the ad-hoc SQL and
        prompt-analysis paths, where the result-set size is not known in advance.
        The connection is a READ ONLY transaction with a bounded statement
        timeout (see :meth:`_configure`), so this cannot mutate or overload the
        database regardless of the query text.
        """
        pool = self._require_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                # Fetch one more than max_rows: if we get it back, we know there
                # were extra rows, so we can report truncation without counting the
                # whole result set. The extra row is then sliced off.
                rows = await cur.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                rows = rows[:max_rows]
                # cur.description carries the column metadata; preserve SELECT order
                # for the caller (empty list for statements that return no columns).
                columns = (
                    [desc.name for desc in cur.description] if cur.description else []
                )
        return columns, rows, truncated

    def clamp_limit(self, limit: int | None, default: int = 25) -> int:
        """Clamp a user-supplied row limit into a safe range."""
        # Fall back to `default` when unset, then force the value into
        # [1, max_row_limit] so a caller can't request 0/negative or an unbounded
        # number of rows. int() coerces string/float inputs to an integer.
        if limit is None:
            limit = default
        return max(1, min(int(limit), self._config.max_row_limit))
