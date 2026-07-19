"""Data-quality inspection over the pet-grooming schema.

Each check reports how many rows violate an expectation (missing optional data,
broken invariants, or likely integrity gaps) against a denominator, so the
frontend can render a completeness/health snapshot. All queries are read-only
aggregates over the same tables the analytics tools use.
"""

from __future__ import annotations

from typing import Any

from ..config import SUCCESSFUL_PAYMENT_STATUSES
from ..database import Database
from ..models import jsonable

# severity → how a non-zero count should be treated.
#   "integrity"    : any violation is bad (broken invariant / impossible value)
#   "completeness" : missing optional data — a warning, not an error
#   "info"         : neutral context, never flagged
_INTEGRITY = "integrity"
_COMPLETENESS = "completeness"
_INFO = "info"


def _status(severity: str, count: int, total: int) -> str:
    """Map a check's raw count to ok / warn / bad for the UI."""
    if severity == _INFO or count == 0:
        return "ok"
    if severity == _INTEGRITY:
        return "bad"
    # completeness: scale by how much of the population is affected
    ratio = (count / total) if total else 0
    return "bad" if ratio >= 0.25 else "warn"


async def get_data_quality(db: Database) -> dict[str, Any]:
    """Return a structured data-quality report across all core tables."""
    users = await db.fetchrow(
        """
        SELECT
            count(*)                                                        AS total,
            count(*) FILTER (WHERE email IS NULL OR btrim(email) = '')      AS missing_email,
            count(*) FILTER (WHERE phone IS NULL OR btrim(phone) = '')      AS missing_phone,
            count(*) FILTER (WHERE address IS NULL OR btrim(address) = '')  AS missing_address,
            count(*) FILTER (WHERE NOT is_active)                           AS inactive
        FROM users
        """
    ) or {}

    pets = await db.fetchrow(
        """
        SELECT
            count(*)                                          AS total,
            count(*) FILTER (WHERE breed_id IS NULL)          AS missing_breed,
            count(*) FILTER (WHERE date_of_birth IS NULL)     AS missing_dob,
            count(*) FILTER (WHERE weight_kg IS NULL)         AS missing_weight,
            count(*) FILTER (WHERE date_of_birth > now())     AS future_dob
        FROM pets
        """
    ) or {}

    appts = await db.fetchrow(
        """
        SELECT
            count(*)                                                          AS total,
            count(*) FILTER (WHERE scheduled_end <= scheduled_start)          AS end_before_start,
            count(*) FILTER (WHERE aps.n = 0)                                 AS no_services
        FROM appointments a
        LEFT JOIN LATERAL (
            SELECT count(*) AS n FROM appointment_services s
            WHERE s.appointment_id = a.id
        ) aps ON true
        """
    ) or {}

    payments = await db.fetchrow(
        f"""
        SELECT
            count(*)                                                          AS total,
            count(*) FILTER (WHERE amount <= 0)                               AS nonpositive_amount,
            count(*) FILTER (
                WHERE lower(status::text) = ANY(%(successful)s) AND paid_at IS NULL
            )                                                                 AS paid_without_timestamp
        FROM payments
        """,
        {"successful": list(SUCCESSFUL_PAYMENT_STATUSES)},
    ) or {}

    services = await db.fetchrow(
        """
        SELECT
            count(*)                                          AS total,
            count(*) FILTER (WHERE base_price <= 0)           AS nonpositive_price,
            count(*) FILTER (WHERE base_duration_minutes <= 0) AS nonpositive_duration
        FROM services
        """
    ) or {}

    # Revenue leakage: appointments marked completed but with no payment row.
    leakage = await db.fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE lower(a.status::text) = 'completed') AS completed,
            count(*) FILTER (
                WHERE lower(a.status::text) = 'completed'
                  AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.appointment_id = a.id)
            ) AS completed_without_payment
        FROM appointments a
        """
    ) or {}

    def check(key, label, description, table, severity, count, total):
        count = int(count or 0)
        total = int(total or 0)
        return {
            "key": key,
            "label": label,
            "description": description,
            "table": table,
            "severity": severity,
            "count": count,
            "total": total,
            "status": _status(severity, count, total),
        }

    u_total = users.get("total", 0)
    p_total = pets.get("total", 0)
    a_total = appts.get("total", 0)
    pay_total = payments.get("total", 0)
    s_total = services.get("total", 0)

    checks = [
        check("users_missing_email", "Customers missing email",
              "Accounts with a null or blank email address.",
              "users", _COMPLETENESS, users.get("missing_email"), u_total),
        check("users_missing_phone", "Customers missing phone",
              "Accounts with no phone number on file.",
              "users", _COMPLETENESS, users.get("missing_phone"), u_total),
        check("users_missing_address", "Customers missing address",
              "Accounts with no address on file.",
              "users", _COMPLETENESS, users.get("missing_address"), u_total),
        check("users_inactive", "Inactive customers",
              "Deactivated customer accounts (informational).",
              "users", _INFO, users.get("inactive"), u_total),

        check("pets_missing_breed", "Pets missing breed",
              "Pets with no linked breed, which weakens breed/size analytics.",
              "pets", _COMPLETENESS, pets.get("missing_breed"), p_total),
        check("pets_missing_dob", "Pets missing date of birth",
              "Pets with no date of birth recorded.",
              "pets", _COMPLETENESS, pets.get("missing_dob"), p_total),
        check("pets_missing_weight", "Pets missing weight",
              "Pets with no weight recorded.",
              "pets", _COMPLETENESS, pets.get("missing_weight"), p_total),
        check("pets_future_dob", "Pets with future birth date",
              "Impossible date of birth in the future.",
              "pets", _INTEGRITY, pets.get("future_dob"), p_total),

        check("appt_end_before_start", "Appointments ending before they start",
              "scheduled_end is at or before scheduled_start.",
              "appointments", _INTEGRITY, appts.get("end_before_start"), a_total),
        check("appt_no_services", "Appointments with no services",
              "Booked appointments that have no service line items.",
              "appointments", _COMPLETENESS, appts.get("no_services"), a_total),

        check("pay_nonpositive_amount", "Payments with non-positive amount",
              "Payment rows with an amount of zero or less.",
              "payments", _INTEGRITY, payments.get("nonpositive_amount"), pay_total),
        check("pay_paid_without_timestamp", "Successful payments missing paid_at",
              "Payments in a successful status but with no paid_at timestamp.",
              "payments", _COMPLETENESS, payments.get("paid_without_timestamp"), pay_total),

        check("svc_nonpositive_price", "Services with non-positive price",
              "Services priced at zero or less.",
              "services", _INTEGRITY, services.get("nonpositive_price"), s_total),
        check("svc_nonpositive_duration", "Services with non-positive duration",
              "Services with a base duration of zero or less.",
              "services", _INTEGRITY, services.get("nonpositive_duration"), s_total),

        check("appt_completed_without_payment", "Completed appointments without payment",
              "Completed appointments with no payment record (possible revenue leakage).",
              "appointments", _COMPLETENESS,
              leakage.get("completed_without_payment"), leakage.get("completed", 0)),
    ]

    issue_count = sum(1 for c in checks if c["status"] != "ok")
    bad_count = sum(1 for c in checks if c["status"] == "bad")

    # A single 0-100 health score: start at 100, subtract weighted penalties.
    score = 100.0
    for c in checks:
        if c["severity"] == _INFO or c["count"] == 0 or not c["total"]:
            continue
        ratio = c["count"] / c["total"]
        weight = 30 if c["severity"] == _INTEGRITY else 12
        score -= min(weight, weight * ratio + (5 if c["severity"] == _INTEGRITY else 0))
    score = max(0, round(score))

    return jsonable(
        {
            "totals": {
                "users": u_total,
                "pets": p_total,
                "appointments": a_total,
                "payments": pay_total,
                "services": s_total,
            },
            "health_score": score,
            "checks_run": len(checks),
            "issues_found": issue_count,
            "critical_issues": bad_count,
            "checks": checks,
        }
    )
