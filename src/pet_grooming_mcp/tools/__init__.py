"""Analytics tool implementations.

Each module contains plain ``async`` functions that take a :class:`Database`
plus typed parameters and return JSON-serializable dictionaries. Keeping the
query logic free of MCP plumbing makes it independently testable; ``server.py``
wraps these functions as MCP tools.
"""
