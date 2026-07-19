"""FastAPI application exposing read-only analytics to the web dashboard.

Reuses the exact same :class:`~pet_grooming_mcp.config.Config` and read-only
:class:`~pet_grooming_mcp.database.Database` as the MCP server, so every HTTP
endpoint inherits the READ ONLY connection pool and bounded statement timeout.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..config import Config
from ..database import Database
from ..tools import appointments, overview, payments, pets, services, users
from . import analyze as analyze_mod
from . import data_quality, sql_runner

# psycopg's async pool cannot run on Windows' default ProactorEventLoop. Set the
# selector policy at import time — before uvicorn (CLI or programmatic) creates
# its event loop — so the app works no matter how it is launched.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Single shared, read-only database handle for the app's lifetime.
_config = Config.load()
_db = Database(_config)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await _db.connect()
    try:
        yield
    finally:
        await _db.close()


app = FastAPI(
    title="Pet Grooming Analytics API",
    description="Read-only analytics over the pet-grooming database.",
    version="0.1.0",
    lifespan=_lifespan,
)

# Allow the Next.js dev server (and any origin in dev) to call the API.
_origins = os.getenv("WEB_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class QueryRequest(BaseModel):
    sql: str = Field(..., description="A single read-only SELECT statement.")
    max_rows: int = Field(sql_runner.DEFAULT_MAX_ROWS, ge=1, le=sql_runner.DEFAULT_MAX_ROWS)


class AnalyzeRequest(BaseModel):
    prompt: str = Field(..., description="A natural-language analytics question.")
    max_rows: int = Field(500, ge=1, le=500)


# --------------------------------------------------------------------------- #
# Health & capabilities
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "analyze_enabled": analyze_mod.is_configured()}


# --------------------------------------------------------------------------- #
# Statistics snapshot — one call powering the whole tab
# --------------------------------------------------------------------------- #
@app.get("/api/statistics")
async def statistics() -> dict[str, Any]:
    """Aggregate every headline metric into a single snapshot payload."""
    (
        business,
        user_stats,
        pet_stats,
        appointment_stats,
        appts_by_status,
        service_stats,
        popular_services,
        payment_stats,
        revenue_summary,
        top_customers,
    ) = await asyncio.gather(
        overview.get_business_overview(_db),
        overview.get_user_statistics(_db),
        overview.get_pet_statistics(_db),
        appointments.get_appointment_statistics(_db),
        appointments.get_appointments_by_status(_db),
        services.get_service_statistics(_db),
        services.get_popular_services(_db, days=90, limit=10),
        payments.get_payment_statistics(_db),
        payments.get_revenue_summary(_db, group_by="month"),
        users.get_top_customers(_db, limit=10, by="spend"),
    )
    return {
        "business_overview": business,
        "user_statistics": user_stats,
        "pet_statistics": pet_stats,
        "appointment_statistics": appointment_stats,
        "appointments_by_status": appts_by_status,
        "service_statistics": service_stats,
        "popular_services": popular_services,
        "payment_statistics": payment_stats,
        "revenue_summary": revenue_summary,
        "top_customers": top_customers,
    }


# --------------------------------------------------------------------------- #
# Data quality snapshot
# --------------------------------------------------------------------------- #
@app.get("/api/data-quality")
async def data_quality_report() -> dict[str, Any]:
    return await data_quality.get_data_quality(_db)


# --------------------------------------------------------------------------- #
# Schema (helps the SQL maker)
# --------------------------------------------------------------------------- #
@app.get("/api/schema")
async def schema() -> dict[str, Any]:
    """Return table/column metadata for the query-builder helper."""
    rows = await _db.fetch(
        """
        SELECT table_name, column_name, data_type, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        tables.setdefault(r["table_name"], []).append(
            {"column": r["column_name"], "type": r["data_type"]}
        )
    return {
        "tables": [
            {"name": name, "columns": cols} for name, cols in sorted(tables.items())
        ]
    }


# --------------------------------------------------------------------------- #
# Ad-hoc SQL query maker
# --------------------------------------------------------------------------- #
@app.post("/api/query")
async def run_query(req: QueryRequest) -> dict[str, Any]:
    try:
        return await sql_runner.run_query(_db, req.sql, max_rows=req.max_rows)
    except sql_runner.SqlValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # database / syntax errors → 400 with the message
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Natural-language analyze (Claude -> SQL + chart)
# --------------------------------------------------------------------------- #
@app.post("/api/analyze")
async def run_analyze(req: AnalyzeRequest) -> dict[str, Any]:
    try:
        return await analyze_mod.analyze(_db, req.prompt, max_rows=req.max_rows)
    except analyze_mod.AnalyzeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def main() -> None:
    """Run the API server (``pet-grooming-web`` / ``python -m ...web.app``).

    On Windows, psycopg's async pool cannot use the ``ProactorEventLoop``. Modern
    uvicorn (>= 0.36) creates the loop via a ``loop_factory`` that hard-codes
    ProactorEventLoop on Windows, so setting an event-loop *policy* isn't enough —
    we must run uvicorn's server on a loop we create ourselves. We therefore drive
    ``Server.serve()`` through an :class:`asyncio.Runner` with a selector loop.
    Do NOT launch this app with the bare ``uvicorn`` CLI on Windows; use this
    entry point instead.
    """
    import uvicorn

    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8000"))
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))

    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
