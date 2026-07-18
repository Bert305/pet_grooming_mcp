"""MCP server entry point.

Registers the read-only analytics tools with a FastMCP server and runs it over
STDIO so it can be launched directly by Claude Desktop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.server.fastmcp import FastMCP

from .config import Config
from .database import Database
from .tools import appointments, overview, payments, pets, services, users

logger = logging.getLogger("pet_grooming_mcp")

# Load configuration and build the database handle at import time so that any
# misconfiguration surfaces immediately with a clear message.
_config = Config.load()
_db = Database(_config)


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Open the connection pool for the lifetime of the server."""
    await _db.connect()
    logger.info("Connected to database; pet grooming analytics MCP ready.")
    try:
        yield {}
    finally:
        await _db.close()
        logger.info("Database connection pool closed.")


mcp = FastMCP(
    "pet-grooming-analytics",
    instructions=(
        "Read-only analytics for a pet-grooming business backed by Supabase "
        "Postgres. Use these tools to answer questions about customers, pets, "
        "appointments, services, and payments. All tools are read-only."
    ),
    lifespan=_lifespan,
)


# --------------------------------------------------------------------------- #
# Overview tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def get_business_overview() -> dict[str, Any]:
    """Headline counts (users, pets, appointments, services) and total revenue."""
    return await overview.get_business_overview(_db)


@mcp.tool()
async def get_user_statistics(
    created_after: str | None = None, created_before: str | None = None
) -> dict[str, Any]:
    """Customer counts, active/inactive split, and average pets per customer.

    Optionally bound the "users created in range" figure with ISO dates
    (YYYY-MM-DD) via ``created_after`` / ``created_before``.
    """
    return await overview.get_user_statistics(_db, created_after, created_before)


@mcp.tool()
async def get_pet_statistics() -> dict[str, Any]:
    """Pet counts broken down by species, breed, and size category."""
    return await overview.get_pet_statistics(_db)


# --------------------------------------------------------------------------- #
# Appointment tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def get_appointment_statistics(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    species: str | None = None,
) -> dict[str, Any]:
    """Aggregate appointment metrics with optional date/status/species filters."""
    return await appointments.get_appointment_statistics(
        _db, start_date, end_date, status, species
    )


@mcp.tool()
async def get_appointments_by_status(
    start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Count of appointments grouped by status, optionally within a date range."""
    return await appointments.get_appointments_by_status(_db, start_date, end_date)


@mcp.tool()
async def get_upcoming_appointments(
    days_ahead: int = 7, limit: int = 25
) -> dict[str, Any]:
    """Upcoming non-cancelled appointments with pet, owner, and services."""
    return await appointments.get_upcoming_appointments(_db, days_ahead, limit)


# --------------------------------------------------------------------------- #
# Search tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def search_users(
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    active_only: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    """Search customers by partial name, email, or phone."""
    return await users.search_users(_db, name, email, phone, active_only, limit)


@mcp.tool()
async def search_pets(
    pet_name: str | None = None,
    owner_name: str | None = None,
    species: str | None = None,
    breed: str | None = None,
    active_only: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    """Search pets by name, owner, species, or breed."""
    return await pets.search_pets(
        _db, pet_name, owner_name, species, breed, active_only, limit
    )


@mcp.tool()
async def get_user_details(user_id: int) -> dict[str, Any]:
    """Full customer profile: contact info, pets, appointment count, and spend."""
    return await users.get_user_details(_db, user_id)


@mcp.tool()
async def get_top_customers(limit: int = 10, by: str = "spend") -> dict[str, Any]:
    """Rank customers by lifetime 'spend' (default) or 'appointments'."""
    return await users.get_top_customers(_db, limit, by)


@mcp.tool()
async def get_pet_appointment_history(
    pet_id: int, limit: int = 25
) -> dict[str, Any]:
    """A pet's appointment history (most recent first) including services."""
    return await pets.get_pet_appointment_history(_db, pet_id, limit)


# --------------------------------------------------------------------------- #
# Service tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def get_service_statistics() -> dict[str, Any]:
    """Service catalogue with pricing, duration, and lifetime booking counts."""
    return await services.get_service_statistics(_db)


@mcp.tool()
async def get_popular_services(days: int = 90, limit: int = 10) -> dict[str, Any]:
    """Most-booked services over the last N days."""
    return await services.get_popular_services(_db, days, limit)


@mcp.tool()
async def get_service_revenue(
    start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Realised revenue attributed to each service over an optional date range."""
    return await services.get_service_revenue(_db, start_date, end_date)


# --------------------------------------------------------------------------- #
# Payment tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def get_payment_statistics(
    start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Payment totals, realised revenue, and breakdowns by status and method."""
    return await payments.get_payment_statistics(_db, start_date, end_date)


@mcp.tool()
async def get_revenue_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    group_by: str = "month",
) -> dict[str, Any]:
    """Realised-revenue time series bucketed by day/week/month/year."""
    return await payments.get_revenue_summary(_db, start_date, end_date, group_by)


def main() -> None:
    """Console-script entry point: run the MCP server over STDIO."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    # psycopg's async mode cannot run on Windows' default ProactorEventLoop;
    # force the SelectorEventLoop policy before FastMCP starts its loop.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    mcp.run()


if __name__ == "__main__":
    main()
