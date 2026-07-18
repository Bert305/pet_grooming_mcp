"""Entry point for the Pet Grooming Analytics MCP server.

Mirrors the launch pattern of the other local MCP servers so it can be started
with:  uv --directory <this-folder> run mcp_server.py
"""

from pet_grooming_mcp.server import main

if __name__ == "__main__":
    main()
