"""One-click connectors for well-known MCP servers.

Each entry is a template for one ``mcpServers`` entry in ``mcp.json``. Entries
were checked against the vendors' own documentation (November 2026; see each
``docs`` link). Connecting writes the entry under the connector id; secrets
never enter ``mcp.json``: the entry references ``${VAR}`` and the value goes
to ``<data_dir>/mcp.env`` (0600) or the process environment
(``app/agent/mcp/config.py``).

Inputs:
``path``    an absolute directory or file on the server, substituted into the
            template wherever ``{key}`` appears;
``text``    a plain value substituted the same way;
``secret``  a token: the template references ``${env}``; a value given at
            connect time is stored in the secrets file, otherwise the variable
            must already be set in the environment.

Servers that only support interactive OAuth or vendor-approved clients (Figma
remote, Slack, Google Workspace) are left out: a headless backend cannot
complete their sign-in. Their local or token alternatives are listed instead.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.agent.mcp.config import read_env_file, read_raw_config, referenced_vars, set_env_value, write_raw_config


@dataclass(frozen=True)
class Input:
    key: str
    label: str
    kind: str                       # "path" | "text" | "secret"
    required: bool = True
    env: Optional[str] = None       # secret: the variable the template references
    default: Optional[str] = None
    help: str = ""

    def public(self) -> dict:
        out = {"key": self.key, "label": self.label, "kind": self.kind, "required": self.required}
        for name in ("env", "default", "help"):
            if getattr(self, name):
                out[name] = getattr(self, name)
        return out


@dataclass(frozen=True)
class Connector:
    id: str
    name: str
    vendor: str
    category: str
    description: str
    config: dict
    docs: str
    auth: str = "none"              # "none" | "token" | "optional-token"
    inputs: tuple[Input, ...] = ()
    requires: tuple[str, ...] = ()  # executables that must be on PATH (npx, uvx, docker)
    notes: str = ""
    tools: tuple[str, ...] = field(default_factory=tuple)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "vendor": self.vendor, "category": self.category,
                "description": self.description, "docs": self.docs, "auth": self.auth,
                "inputs": [i.public() for i in self.inputs], "requires": list(self.requires),
                "notes": self.notes, "tools": list(self.tools),
                "transport": "remote" if "url" in self.config else "local"}


def _http(url: str, headers: dict | None = None) -> dict:
    return {"url": url, "transport": "http", **({"headers": headers} if headers else {})}


def _bearer(env: str) -> dict:
    return {"Authorization": f"Bearer ${{{env}}}"}


def _optional_bearer(env: str) -> dict:
    # An unset optional token expands to "Bearer "; the hub then drops the header.
    return {"Authorization": f"Bearer ${{{env}:-}}"}


CATALOG: tuple[Connector, ...] = (
    # ── Documentation and knowledge ────────────────────────────────────
    Connector("microsoft-learn", "Microsoft Learn", "Microsoft", "Docs",
              "Search and fetch official Microsoft and Azure documentation and code samples.",
              _http("https://learn.microsoft.com/api/mcp"),
              "https://learn.microsoft.com/en-us/training/support/mcp-developer-reference",
              tools=("microsoft_docs_search", "microsoft_code_sample_search", "microsoft_docs_fetch")),
    Connector("context7", "Context7", "Upstash", "Docs",
              "Up-to-date, version-specific library documentation and code examples.",
              _http("https://mcp.context7.com/mcp", _optional_bearer("CONTEXT7_API_KEY")),
              "https://context7.com/docs/resources/all-clients", auth="optional-token",
              inputs=(Input("api_key", "API key (optional, raises rate limits)", "secret", required=False,
                            env="CONTEXT7_API_KEY"),)),
    Connector("deepwiki", "DeepWiki", "Cognition", "Docs",
              "Ask questions about any public GitHub repository and read its generated wiki.",
              _http("https://mcp.deepwiki.com/mcp"), "https://docs.devin.ai/work-with-devin/deepwiki-mcp",
              tools=("read_wiki_structure", "read_wiki_contents", "ask_question")),
    Connector("cloudflare-docs", "Cloudflare Docs", "Cloudflare", "Docs",
              "Search Cloudflare's developer documentation.",
              _http("https://docs.mcp.cloudflare.com/mcp"), "https://github.com/cloudflare/mcp-server-cloudflare"),
    Connector("aws-docs", "AWS Documentation", "AWS Labs", "Docs",
              "Search, read and get recommendations from AWS documentation.",
              {"command": "uvx", "args": ["awslabs.aws-documentation-mcp-server@latest"],
               "env": {"FASTMCP_LOG_LEVEL": "ERROR"}},
              "https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server", requires=("uvx",)),
    Connector("huggingface", "Hugging Face", "Hugging Face", "AI / ML",
              "Search models, datasets, Spaces and papers on the Hugging Face Hub.",
              _http("https://huggingface.co/mcp", _optional_bearer("HF_TOKEN")),
              "https://huggingface.co/docs/hub/en/hf-mcp-server", auth="optional-token",
              inputs=(Input("token", "Access token (optional; unlocks the full tool set)", "secret",
                            required=False, env="HF_TOKEN"),)),

    # ── Mathematics and science ────────────────────────────────────────
    Connector("wolfram", "Wolfram Cloud", "Wolfram", "Math & science",
              "Wolfram Language evaluation and Wolfram|Alpha knowledge through Wolfram's free cloud MCP server.",
              _http("https://agenttools.wolfram.com/mcp"), "https://support.wolfram.com/75237"),
    Connector("matlab", "MATLAB", "MathWorks", "Math & science",
              "Run MATLAB code, scripts and tests in a local MATLAB through the MATLAB MCP Core Server.",
              {"command": "{server}", "args": ["--matlab-root={matlab_root}", "--initialize-matlab-on-startup=true"]},
              "https://github.com/matlab/matlab-mcp-core-server",
              inputs=(Input("server", "Path to the downloaded matlab-mcp-server binary", "path"),
                      Input("matlab_root", "MATLAB root folder (without /bin)", "path",
                            help="e.g. /Applications/MATLAB_R2025b.app")),
              notes="Requires a licensed local MATLAB installation."),
    Connector("jupyter", "Jupyter", "Datalayer", "Math & science",
              "Read, edit and execute cells of notebooks on a running JupyterLab server.",
              {"command": "uvx", "args": ["jupyter-mcp-server@latest"],
               "env": {"JUPYTER_URL": "{url}", "JUPYTER_TOKEN": "${JUPYTER_TOKEN}", "ALLOW_IMG_OUTPUT": "true"}},
              "https://github.com/datalayer/jupyter-mcp-server", auth="token", requires=("uvx",),
              inputs=(Input("url", "JupyterLab URL", "text", default="http://localhost:8888"),
                      Input("token", "Jupyter server token", "secret", env="JUPYTER_TOKEN"))),
    Connector("arxiv", "arXiv", "community (blazickjp)", "Math & science",
              "Search arXiv, download papers and read them.",
              {"command": "uvx", "args": ["arxiv-mcp-server"]},
              "https://github.com/blazickjp/arxiv-mcp-server", requires=("uvx",)),
    Connector("zotero", "Zotero", "community (54yyyu)", "Math & science",
              "Search and read your local Zotero library, annotations and full text.",
              {"command": "zotero-mcp", "args": [], "env": {"ZOTERO_LOCAL": "true"}},
              "https://github.com/54yyyu/zotero-mcp", requires=("zotero-mcp",),
              notes="Install first: uv tool install zotero-mcp-server. Zotero must be running."),
    Connector("wandb", "Weights & Biases", "Weights & Biases", "AI / ML",
              "Query W&B runs, metrics, sweeps and Weave traces.",
              _http("https://mcp.withwandb.com/mcp", _bearer("WANDB_API_KEY")),
              "https://github.com/wandb/wandb-mcp-server", auth="token",
              inputs=(Input("api_key", "W&B API key", "secret", env="WANDB_API_KEY"),)),

    # ── Development ────────────────────────────────────────────────────
    Connector("github", "GitHub", "GitHub", "Development",
              "Repositories, issues, pull requests, Actions and code search through GitHub's official server.",
              _http("https://api.githubcopilot.com/mcp/", _bearer("GITHUB_PERSONAL_ACCESS_TOKEN")),
              "https://github.com/github/github-mcp-server", auth="token",
              inputs=(Input("token", "Personal access token", "secret", env="GITHUB_PERSONAL_ACCESS_TOKEN",
                            help="Fine-grained token with the repository permissions you want the agent to have."),)),
    Connector("linear", "Linear", "Linear", "Development",
              "Find, create and update Linear issues, projects and comments.",
              _http("https://mcp.linear.app/mcp", _bearer("LINEAR_API_KEY")),
              "https://linear.app/docs/mcp", auth="token",
              inputs=(Input("api_key", "Linear API key", "secret", env="LINEAR_API_KEY"),)),
    Connector("sentry", "Sentry", "Sentry", "Development",
              "Look up Sentry issues, events, stack traces and releases.",
              {"command": "npx", "args": ["@sentry/mcp-server@latest"],
               "env": {"SENTRY_AUTH_TOKEN": "${SENTRY_AUTH_TOKEN}"}},
              "https://github.com/getsentry/sentry-mcp", auth="token", requires=("npx",),
              inputs=(Input("token", "Sentry user auth token", "secret", env="SENTRY_AUTH_TOKEN"),)),
    Connector("supabase", "Supabase", "Supabase", "Development",
              "Manage Supabase projects: tables, SQL, migrations, logs and docs.",
              _http("https://mcp.supabase.com/mcp?read_only=true", _bearer("SUPABASE_ACCESS_TOKEN")),
              "https://supabase.com/docs/guides/getting-started/mcp", auth="token",
              inputs=(Input("token", "Supabase personal access token", "secret", env="SUPABASE_ACCESS_TOKEN"),),
              notes="Connected read-only; remove read_only=true from the URL in mcp.json to allow writes."),
    Connector("stripe", "Stripe", "Stripe", "Development",
              "Stripe API and documentation: customers, payments, products, docs search.",
              _http("https://mcp.stripe.com", _bearer("STRIPE_API_KEY")),
              "https://docs.stripe.com/mcp", auth="token",
              inputs=(Input("api_key", "Stripe agent (restricted) API key", "secret", env="STRIPE_API_KEY",
                            help="Use a test-mode key tagged for agents; full secret keys are rejected."),)),
    Connector("atlassian", "Atlassian (Jira, Confluence)", "Atlassian", "Productivity",
              "Search and update Jira issues and Confluence pages through Atlassian's Rovo MCP server.",
              _http("https://mcp.atlassian.com/v2/mcp", _bearer("ATLASSIAN_API_KEY")),
              "https://support.atlassian.com/atlassian-rovo-mcp-server/", auth="token",
              inputs=(Input("api_key", "Service-account API key", "secret", env="ATLASSIAN_API_KEY"),)),
    Connector("notion", "Notion", "Notion", "Productivity",
              "Search, read and edit Notion pages and databases.",
              _http("https://mcp.notion.com/mcp", _bearer("NOTION_MCP_TOKEN")),
              "https://developers.notion.com/guides/mcp/get-started-with-mcp", auth="token",
              inputs=(Input("token", "Notion MCP token", "secret", env="NOTION_MCP_TOKEN"),),
              notes="Token access needs a Notion Business or Enterprise plan."),

    # ── Browsers ───────────────────────────────────────────────────────
    Connector("playwright", "Playwright", "Microsoft", "Browser",
              "Drive a real browser: navigate, click, fill forms, read the page's accessibility tree.",
              {"command": "npx", "args": ["@playwright/mcp@latest", "--headless", "--isolated"]},
              "https://github.com/microsoft/playwright-mcp", requires=("npx",)),
    Connector("chrome-devtools", "Chrome DevTools", "Google", "Browser",
              "Inspect and debug pages in Chrome: console, network, performance traces, screenshots.",
              {"command": "npx", "args": ["-y", "chrome-devtools-mcp@latest", "--headless", "--isolated"]},
              "https://github.com/ChromeDevTools/chrome-devtools-mcp", requires=("npx",)),

    # ── Local files and tools ──────────────────────────────────────────
    Connector("filesystem", "Filesystem", "MCP reference servers", "Local",
              "Read and write files inside one directory on this server.",
              {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{directory}"]},
              "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem", requires=("npx",),
              inputs=(Input("directory", "Directory to expose", "path"),)),
    Connector("git", "Git", "MCP reference servers", "Local",
              "Read and operate on a local Git repository: status, diff, log, commit, branches.",
              {"command": "uvx", "args": ["mcp-server-git", "--repository", "{repository}"]},
              "https://github.com/modelcontextprotocol/servers/tree/main/src/git", requires=("uvx",),
              inputs=(Input("repository", "Repository path", "path"),)),
    Connector("fetch", "Fetch", "MCP reference servers", "Local",
              "Fetch a URL and convert the page to Markdown.",
              {"command": "uvx", "args": ["mcp-server-fetch"]},
              "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch", requires=("uvx",)),
    Connector("memory", "Knowledge graph memory", "MCP reference servers", "Local",
              "A persistent local knowledge graph of entities, relations and observations.",
              {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
              "https://github.com/modelcontextprotocol/servers/tree/main/src/memory", requires=("npx",)),
    Connector("excel", "Excel", "community (haris-musa)", "Local",
              "Create, read and edit Excel workbooks (formulas, charts, pivot tables) without Excel installed.",
              {"command": "uvx", "args": ["excel-mcp-server", "stdio"], "env": {"EXCEL_FILES_PATH": "{directory}"}},
              "https://github.com/haris-musa/excel-mcp-server", requires=("uvx",),
              inputs=(Input("directory", "Folder with the workbooks", "path"),)),
    Connector("figma-desktop", "Figma (desktop app)", "Figma", "Design",
              "Design context, variables and code from the open Figma file via the Figma desktop app's local server.",
              _http("http://127.0.0.1:3845/mcp"), "https://developers.figma.com/docs/figma-mcp-server/",
              notes="Enable the MCP server in Figma's Dev Mode first; needs a Dev or Full seat."),
    Connector("blender", "Blender", "community (ahujasid)", "Design",
              "Create and modify 3D scenes in a running Blender.",
              {"command": "uvx", "args": ["blender-mcp"], "env": {"BLENDER_HOST": "localhost", "BLENDER_PORT": "9876"}},
              "https://github.com/ahujasid/blender-mcp", requires=("uvx",),
              notes="Install and enable the Blender add-on from the project first."),
    Connector("docker", "Docker MCP Toolkit", "Docker", "Local",
              "Every server enabled in Docker Desktop's MCP Toolkit, through one gateway.",
              {"command": "docker", "args": ["mcp", "gateway", "run"]},
              "https://github.com/docker/mcp-gateway", requires=("docker",)),
)

BY_ID = {c.id: c for c in CATALOG}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
CATALOG_MARK = "x-matopt-connector"


def _substitute(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _substitute(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, values) for v in value]
    return value


def build_entry(connector: Connector, values: dict[str, str]) -> dict:
    """The ``mcpServers`` entry for ``connector`` with non-secret inputs filled in."""
    plain: dict[str, str] = {}
    for inp in connector.inputs:
        if inp.kind == "secret":
            continue
        raw = str(values.get(inp.key) or inp.default or "").strip()
        if not raw:
            if inp.required:
                raise ValueError(f"{inp.label} is required")
            continue
        if "\n" in raw or "\x00" in raw:
            raise ValueError(f"{inp.label} must be a single line")
        if inp.kind == "path":
            path = Path(raw).expanduser()
            if not path.is_absolute():
                raise ValueError(f"{inp.label} must be an absolute path")
            if not path.exists():
                raise ValueError(f"{inp.label} does not exist on the server: {path}")
            raw = str(path)
        plain[inp.key] = raw
    entry = _substitute(copy.deepcopy(connector.config), plain)
    entry[CATALOG_MARK] = connector.id
    return entry


class ConnectorManager:
    """Reads and edits ``mcp.json`` and the secrets file for catalog connectors."""

    def __init__(self, config_path: Path, env_path: Path):
        self.config_path = Path(config_path)
        self.env_path = Path(env_path)

    def _servers(self) -> dict:
        return read_raw_config(self.config_path).get("mcpServers", {})

    def list(self, status: Optional[list[dict]] = None) -> list[dict]:
        servers = self._servers()
        state = {row.get("name"): row for row in status or []}
        env = {**os.environ, **read_env_file(self.env_path)}
        stored = read_env_file(self.env_path)
        out = []
        for c in CATALOG:
            row = c.public()
            entry = servers.get(c.id)
            row["installed"] = isinstance(entry, dict)
            row["missing"] = [r for r in sorted(referenced_vars(entry if row["installed"] else c.config))
                              if not env.get(r) and not _optional(c, r)]
            row["secrets"] = {i.env: bool(stored.get(i.env) or os.environ.get(i.env))
                              for i in c.inputs if i.kind == "secret" and i.env}
            row["available"] = all(shutil.which(cmd) for cmd in c.requires)
            if row["installed"]:
                row["enabled"] = entry.get("enabled", True) is not False
                live = state.get(c.id)
                row["state"] = live.get("state") if live else "pending"
                if live and live.get("error"):
                    row["error"] = live["error"]
                if live:
                    row["tool_count"] = live.get("tools", 0)
            out.append(row)
        return out

    def connect(self, connector_id: str, values: dict[str, str], secrets: dict[str, str]) -> dict:
        connector = BY_ID.get(connector_id)
        if connector is None:
            raise KeyError(connector_id)
        entry = build_entry(connector, values)
        for inp in connector.inputs:
            if inp.kind != "secret" or not inp.env:
                continue
            value = (secrets.get(inp.key) or "").strip()
            if value:
                set_env_value(self.env_path, inp.env, value)
            elif inp.required and not (os.environ.get(inp.env) or read_env_file(self.env_path).get(inp.env)):
                raise ValueError(f"{inp.label} is required (or set {inp.env} in the server environment)")
        raw = read_raw_config(self.config_path)
        raw["mcpServers"][connector.id] = entry
        write_raw_config(self.config_path, raw)
        return entry

    def disconnect(self, connector_id: str, forget_secrets: bool = True) -> bool:
        connector = BY_ID.get(connector_id)
        if connector is None:
            raise KeyError(connector_id)
        raw = read_raw_config(self.config_path)
        removed = raw["mcpServers"].pop(connector_id, None) is not None
        if removed:
            write_raw_config(self.config_path, raw)
        if forget_secrets:
            for inp in connector.inputs:
                if inp.kind == "secret" and inp.env and inp.env in read_env_file(self.env_path):
                    set_env_value(self.env_path, inp.env, None)
        return removed

    def set_enabled(self, connector_id: str, enabled: bool) -> bool:
        raw = read_raw_config(self.config_path)
        entry = raw["mcpServers"].get(connector_id)
        if not isinstance(entry, dict):
            return False
        if enabled:
            entry.pop("enabled", None)
        else:
            entry["enabled"] = False
        write_raw_config(self.config_path, raw)
        return True


def _optional(connector: Connector, var: str) -> bool:
    return any(i.env == var and not i.required for i in connector.inputs)
