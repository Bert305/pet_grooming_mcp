"""HTTP layer exposing the read-only analytics to a web frontend.

This package wraps the same read-only :class:`~pet_grooming_mcp.database.Database`
and query tools that the MCP server uses, so the browser dashboard inherits the
identical security posture: pooled connections pinned to ``READ ONLY`` with a
bounded ``statement_timeout``, and only ``SELECT`` traffic on the ad-hoc paths.
"""
