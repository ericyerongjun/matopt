"""stdio MCP entry: python -m app.agent.mcp"""

from app.agent.mcp.sandbox import get_mcp

if __name__ == "__main__":
    get_mcp().run()
