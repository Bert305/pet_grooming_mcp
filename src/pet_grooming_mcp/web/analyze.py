"""Natural-language analytics: prompt -> SQL + chart spec, via the Claude API.

The user's question is sent to Claude Opus 4.8 with the database schema and a
strict output contract. Claude returns a single read-only ``SELECT``, a short
explanation, and a chart specification. The SQL is then run through the same
read-only guard rails as the query maker (:mod:`sql_runner`) — Claude never
touches the database directly, and anything that isn't a lone ``SELECT`` is
rejected before execution.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..database import Database
from . import sql_runner

MODEL = "claude-opus-4-8"

# The schema Claude writes SQL against. Kept in sync with sql/schema.sql.
SCHEMA_DDL = """
-- enums
pet_species        : 'dog','cat','rabbit','bird','other'
appointment_status : 'scheduled','in_progress','completed','cancelled','no_show'
payment_method     : 'card','cash','bank_transfer','online'
payment_status     : 'pending','completed','failed','refunded'

users(id bigint pk, full_name text, email text, phone text, address text,
      preferences text, created_at timestamptz, is_active boolean)

breeds(id bigint pk, species pet_species, name text, size_category text, coat_type text)

pets(id bigint pk, user_id bigint -> users.id, name text, species pet_species,
     breed_id bigint -> breeds.id, date_of_birth date, weight_kg numeric,
     notes text, created_at timestamptz, is_active boolean)

services(id bigint pk, name text, description text, species pet_species,
         base_duration_minutes int, base_price numeric, is_active boolean)

appointments(id bigint pk, pet_id bigint -> pets.id, scheduled_start timestamptz,
             scheduled_end timestamptz, status appointment_status,
             special_instructions text, created_at timestamptz, updated_at timestamptz)

appointment_services(id bigint pk, appointment_id bigint -> appointments.id,
                     service_id bigint -> services.id, price_override numeric,
                     duration_override_minutes int)

payments(id bigint pk, appointment_id bigint -> appointments.id, amount numeric,
         currency char(3), method payment_method, status payment_status,
         paid_at timestamptz)
""".strip()

SYSTEM_PROMPT = f"""You are a senior data analyst for a pet-grooming business. \
You translate natural-language questions into a single read-only PostgreSQL \
query, and you propose how to visualise the result.

DATABASE SCHEMA
{SCHEMA_DDL}

RULES
- Emit exactly ONE statement: a SELECT (optionally a leading WITH ... SELECT). \
Never write INSERT/UPDATE/DELETE/DDL or multiple statements.
- Only reference tables and columns from the schema above.
- Cast enum columns to text when grouping or displaying them (e.g. status::text).
- "Realised revenue" = SUM(payments.amount) WHERE lower(status::text) IN \
('completed','paid','succeeded','captured','settled'). Cancelled appointment \
statuses are 'cancelled' or 'canceled'.
- Always include a LIMIT (<= 500) unless the query is a single aggregate row.
- Give result columns clear, snake_case aliases suitable for a chart.
- Choose the simplest chart that fits: 'bar' for category comparisons, 'line' \
for time series, 'pie' for parts-of-a-whole, 'table' when there is nothing \
meaningful to plot. Set chart.x to the category/time column and chart.y to the \
numeric column(s). Use 'none'/'table' if a chart would not help.
- explanation: one or two plain-English sentences about what the query returns.
"""

# Structured-output contract. Every object sets additionalProperties:false and
# lists its required keys, per the structured-outputs schema rules.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "A single read-only SELECT statement."},
        "explanation": {
            "type": "string",
            "description": "One or two sentences describing the result.",
        },
        "chart": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["bar", "line", "area", "pie", "table", "none"],
                },
                "x": {
                    "type": "string",
                    "description": "Column for the x-axis / category (empty if n/a).",
                },
                "y": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Numeric column(s) to plot.",
                },
                "title": {"type": "string"},
            },
            "required": ["type", "x", "y", "title"],
            "additionalProperties": False,
        },
    },
    "required": ["sql", "explanation", "chart"],
    "additionalProperties": False,
}


class AnalyzeError(RuntimeError):
    """Raised when the analysis cannot be produced or executed."""


def is_configured() -> bool:
    """True if an Anthropic credential is present in the environment."""
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


async def _generate_plan(prompt: str) -> dict[str, Any]:
    """Ask Claude for {sql, explanation, chart} for the user's question."""
    try:
        import anthropic
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise AnalyzeError(
            "The 'anthropic' package is not installed. Install the web extras: "
            "pip install -e '.[web]'."
        ) from exc

    if not is_configured():
        raise AnalyzeError(
            "ANTHROPIC_API_KEY is not set. Add it to your environment or .env to "
            "use the analyze feature."
        )

    client = anthropic.AsyncAnthropic()
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as exc:  # pragma: no cover - network path
        raise AnalyzeError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:  # pragma: no cover - network path
        raise AnalyzeError("Could not reach the Claude API. Check your connection.") from exc

    if response.stop_reason == "refusal":
        raise AnalyzeError("The request was declined by the model's safety system.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise AnalyzeError("The model returned an empty response.")
    try:
        plan = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalyzeError("The model returned malformed JSON.") from exc
    return plan


async def analyze(db: Database, prompt: str, max_rows: int = 500) -> dict[str, Any]:
    """Turn a natural-language prompt into an executed, chart-ready result.

    Returns ``{prompt, explanation, chart, sql, columns, rows, row_count,
    truncated}``. The generated SQL is validated and executed read-only; a bad
    query surfaces as an error the frontend can show alongside the SQL.
    """
    if not prompt or not prompt.strip():
        raise AnalyzeError("Please enter a question to analyze.")

    plan = await _generate_plan(prompt.strip())
    sql = plan.get("sql", "")
    explanation = plan.get("explanation", "")
    chart = plan.get("chart") or {"type": "table", "x": "", "y": [], "title": ""}

    try:
        result = await sql_runner.run_query(db, sql, max_rows=max_rows)
    except sql_runner.SqlValidationError as exc:
        raise AnalyzeError(f"Generated query rejected: {exc}") from exc

    return {
        "prompt": prompt.strip(),
        "explanation": explanation,
        "chart": chart,
        **result,
    }
