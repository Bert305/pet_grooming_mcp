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

### 3. Run

```bash
pet-grooming-mcp
# or
python -m pet_grooming_mcp
```

### 4. Verify with the MCP Inspector (optional)

```bash
npx @modelcontextprotocol/inspector pet-grooming-mcp
```

## Connect to Claude Desktop

Add the server to your Claude Desktop config
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "pet-grooming-analytics": {
      "command": "pet-grooming-mcp",
      "env": {
        "DATABASE_URL": "postgresql://postgres.your-ref:your-password@aws-0-region.pooler.supabase.com:5432/postgres?sslmode=require"
      }
    }
  }
}
```

If `pet-grooming-mcp` isn't on your PATH, use the absolute path to the console
script (or `"command": "python", "args": ["-m", "pet_grooming_mcp"]`), and set a
`"cwd"` so the virtual environment resolves.

Restart Claude Desktop, then try:

- "Give me a business overview."
- "Find all dogs owned by customers named Johnson."
- "Show Bella's appointment history."
- "Which services have been used most during the last 90 days?"
- "What was our revenue by month this year?"

## Development

```bash
pytest            # run the offline test suite (no database required)
```

The tests use a `FakeDatabase` that returns canned rows, so they verify each
tool's output shape and JSON serialization without a live Postgres instance.

## Project layout

```
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
