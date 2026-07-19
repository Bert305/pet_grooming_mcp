# Pet Grooming Analytics MCP

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server
that connects Claude Desktop (or any MCP client) to a Supabase / PostgreSQL
pet-grooming database. It exposes secure, tool-based analytics over customers,
pets, appointments, services, and payments — Claude calls clearly-defined tools
instead of generating unrestricted SQL.

The server runs locally over STDIO and is launched directly by Claude Desktop.

## Why read-only tools instead of raw SQL

- **No arbitrary SQL from the model.** Every tool issues a fixed, parameterised
  query. The client only supplies typed arguments (dates, names, limits).
- **Read-only at the connection layer.** Pooled connections are pinned to
  `READ ONLY` transactions with `default_transaction_read_only=on` and a bounded
  `statement_timeout`.
- **Bounded results.** Row limits are clamped server-side (`MAX_ROW_LIMIT`).
- **Defence in depth.** You are encouraged to point `DATABASE_URL` at a
  dedicated read-only database role (see [`sql/schema.sql`](sql/schema.sql)).

## Tools

### Overview
| Tool | Description |
| --- | --- |
| `get_business_overview` | Headline counts (users, pets, appointments, services) and total revenue. |
| `get_user_statistics` | Active/inactive customers, users created in a date range, avg pets per customer. |
| `get_pet_statistics` | Pet counts by species, breed, and size category. |

### Appointments
| Tool | Description |
| --- | --- |
| `get_appointment_statistics` | Aggregate metrics with optional `start_date`, `end_date`, `status`, `species` filters. |
| `get_appointments_by_status` | Count of appointments grouped by status. |
| `get_upcoming_appointments` | Upcoming non-cancelled appointments with pet, owner, and services. |

### Search & customers
| Tool | Description |
| --- | --- |
| `search_users` | Find customers by partial `name` / `email` / `phone`. |
| `search_pets` | Find pets by `pet_name` / `owner_name` / `species` / `breed`. |
| `get_user_details` | Full customer profile: pets, appointment count, lifetime spend. |
| `get_top_customers` | Rank customers by lifetime spend or appointment count. |
| `get_pet_appointment_history` | A pet's appointment history including booked services. |

### Services
| Tool | Description |
| --- | --- |
| `get_service_statistics` | Catalogue with pricing, duration, and lifetime booking counts. |
| `get_popular_services` | Most-booked services over the last N days. |
| `get_service_revenue` | Realised revenue attributed to each service. |

### Payments
| Tool | Description |
| --- | --- |
| `get_payment_statistics` | Totals, realised revenue, breakdowns by status and method. |
| `get_revenue_summary` | Realised-revenue time series bucketed by day/week/month/year. |

> **Revenue definition:** "realised revenue" sums payments whose status is one of
> `completed`, `paid`, `succeeded`, `captured`, `settled` (see
> [`config.py`](src/pet_grooming_mcp/config.py)). Adjust that list to match your
> `payment_status` enum.

## Setup

### 1. Install

```bash
# with uv (recommended)
uv venv
uv pip install -e ".[dev]"

# or with pip
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate    # macOS/Linux
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
```

Set `DATABASE_URL` to your Supabase Postgres connection string
(Supabase → **Project Settings → Database → Connection string → URI**). The real
`.env` is git-ignored.

If you don't have a database yet, run [`sql/schema.sql`](sql/schema.sql) in the
Supabase SQL editor to create the schema.

## How to run

An MCP server isn't a web app — there's no URL to open. It talks JSON-RPC over
stdin/stdout and is normally launched by an MCP client (Claude Desktop). There
are three ways to run it, depending on what you want to do.

### A. Through Claude Desktop (the real use case)

Configure it once (see [Connect to Claude Desktop](#connect-to-claude-desktop)
below), then fully quit and restart Claude Desktop. Claude launches the server
for you — you don't run anything manually. Check **Settings → Developer**; the
server should show as connected.

### B. Manually in a terminal (to see it start / debug)

```bash
uv run mcp_server.py
```

This is the exact command Claude Desktop uses. It reads your `.env`, connects to
Supabase, then **waits silently** for input on stdin — that is correct behaviour
for an MCP server. If nothing errors, it's working. Press `Ctrl+C` to stop.

> On Windows, launch with `uv run` (or the project's venv) rather than a bare
> `python mcp_server.py`, so the server's async database driver uses a compatible
> event loop.

### C. Interactive testing with the MCP Inspector (recommended)

A browser UI to click each tool and see live results from your database:

```bash
npx @modelcontextprotocol/inspector uv run mcp_server.py
```

### Run the tests (no database required)

```bash
uv run pytest
```

The tests use a `FakeDatabase` that returns canned rows, so they verify each
tool's output shape and JSON serialization without a live Postgres instance.

## Connect to Claude Desktop

Edit your Claude Desktop config
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS) and
add the server. Point `--directory` at this project folder:

```json
{
  "mcpServers": {
    "pet-grooming-analytics": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\dev\\pet_grooming_mcp\\pet_grooming_mcp",
        "run",
        "mcp_server.py"
      ],
      "env": {
        "DATABASE_URL": "postgresql://postgres.your-ref:your-password@aws-0-region.pooler.supabase.com:5432/postgres?sslmode=require"
      }
    }
  }
}
```

Notes:

- Using `uv --directory ... run mcp_server.py` avoids PATH problems — you don't
  need the project's virtual environment to be active or on `PATH`.
- If your password contains a `%`, percent-encode it as `%25` in the URL (other
  reserved characters likewise, e.g. `@` → `%40`).
- The `DATABASE_URL` in `env` can be omitted if it is already set in `.env`.

Then fully quit and restart Claude Desktop and try:

- "Give me a business overview."
- "Find all dogs owned by customers named Johnson."
- "Show Bella's appointment history."
- "Which services have been used most during the last 90 days?"
- "What was our revenue by month this year?"

## Web dashboard (optional)

Alongside the MCP server, this repo ships a browser dashboard so you can *see*
the analytics: a FastAPI backend (`src/pet_grooming_mcp/web/`) that reuses the
exact same read-only `Database` and query tools, and a Next.js frontend
(`../frontend/`). Nothing about the security model changes — the HTTP layer
inherits the `READ ONLY` connection pool and bounded `statement_timeout`, and
the ad-hoc SQL paths reject anything that isn't a single `SELECT`.

### Tabs

- **Statistics Snapshot** — headline KPIs, revenue trend, appointments by
  status, pets by species, top customers. Export the whole view as **JPEG** or
  the metrics as **CSV**.
- **Data Quality Snapshot** — completeness/integrity checks with a 0-100 health
  score. Export as **JPEG** or **CSV**.
- **Analyze (Prompt)** — ask a question in plain English; Claude
  (`claude-opus-4-8`) writes a read-only SQL query, the backend runs it, and the
  result is charted alongside the generated SQL.
- **SQL Query Maker** — write your own `SELECT`, run it, browse the schema, chart
  the result, and export to CSV.

### 1. Backend

```bash
uv pip install -e ".[web]"      # Think of this like a npm install for a json package you run once to activate the dependencies --> adds fastapi, uvicorn, anthropic
# Set ANTHROPIC_API_KEY in .env to enable the "Analyze (Prompt)" tab.
uv run pet-grooming-web         # serves http://127.0.0.1:8000
```

> **Windows:** launch via `pet-grooming-web` (or
> `python -m pet_grooming_mcp.web.app`), **not** the bare `uvicorn` CLI — the
> async Postgres driver needs the selector event loop, which the entry point
> sets up. Host/port/CORS are configurable via `WEB_HOST`, `WEB_PORT`,
> `WEB_CORS_ORIGINS`.

### 2. Frontend

```bash
cd ../frontend
npm install
npm run dev                     # serves http://localhost:3000
```

Point the UI at the backend with `NEXT_PUBLIC_API_BASE` (defaults to
`http://127.0.0.1:8000`); see `frontend/.env.local.example`.

## Project layout

```
mcp_server.py      # entry point: `uv run mcp_server.py`
src/pet_grooming_mcp/
  server.py        # FastMCP server: registers tools, manages the pool lifespan
  config.py        # environment configuration
  database.py      # read-only async connection pool
  tools/           # query logic (overview, users, pets, appointments, services, payments)
  models/          # JSON serialization helpers
sql/schema.sql     # reference schema + read-only role
tests/             # offline tests
```

## License

MIT — see [LICENSE](LICENSE).
