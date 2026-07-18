"""Configuration loading for the Pet Grooming Analytics MCP server.

Configuration is read from environment variables (optionally via a local
``.env`` file). The only required value is the Postgres connection string used
for read-only analytics queries.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Payment statuses that count towards realised revenue. Kept explicit (and
# documented) so the revenue definition is transparent rather than hidden in SQL.
SUCCESSFUL_PAYMENT_STATUSES: tuple[str, ...] = (
    "completed",
    "paid",
    "succeeded",
    "captured",
    "settled",
)

# Appointment statuses treated as cancelled (covers both spellings).
CANCELLED_STATUSES: tuple[str, ...] = ("cancelled", "canceled")


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the server."""

    database_url: str
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    statement_timeout_ms: int = 30_000
    max_row_limit: int = 500
    pool_min_size: int = 1
    pool_max_size: int = 5

    @classmethod
    def load(cls) -> "Config":
        """Build a :class:`Config` from the environment.

        Raises:
            RuntimeError: if no Postgres connection string is configured.
        """
        load_dotenv()

        database_url = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
        if not database_url:
            raise RuntimeError(
                "No database connection string found. Set DATABASE_URL (or "
                "SUPABASE_DB_URL) to your Supabase Postgres connection string. "
                "You can copy it from your Supabase project under "
                "Project Settings -> Database -> Connection string."
            )

        return cls(
            database_url=database_url,
            supabase_url=os.getenv("SUPABASE_URL"),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            statement_timeout_ms=int(os.getenv("STATEMENT_TIMEOUT_MS", "30000")),
            max_row_limit=int(os.getenv("MAX_ROW_LIMIT", "500")),
            pool_min_size=int(os.getenv("POOL_MIN_SIZE", "1")),
            pool_max_size=int(os.getenv("POOL_MAX_SIZE", "5")),
        )
