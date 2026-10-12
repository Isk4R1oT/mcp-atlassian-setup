# atlassian-mcp-setup

Minimal, copy-paste setup for connecting an agent (Claude Code, Codex, or any
MCP client) to **Jira and Confluence self-hosted** through one MCP server:
[`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian).

Both products are served by a **single server process** — Confluence is enabled
by adding it to `TOOLSETS`, not by adding a second entry.

The point of this repo is the credential layout: **no secret ever lives in the
agent config or in git.** The config carries a path to an env file; the env file
lives outside the repository with mode `600`.

## Why not just put the token in the config

The naive setup puts `JIRA_PERSONAL_TOKEN` into `mcpServers.<name>.env` or
exports it from `~/.zshrc`. Both fail in practice:

- The config file becomes a secret and can never be committed or shared.
- Shell-exported variables must be present *before* the agent starts, which
  means every new terminal needs `source ~/.zshrc` first. Miss it and the token
  expands to an empty string, and the server starts unauthenticated instead of
  failing loudly.

Passing `--env-file` fixes both: the server reads the file itself, so the secret
is in exactly one place and the shell no longer matters.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) (provides `uvx`)

## 1. Put the credentials somewhere outside any repository

```bash
mkdir -p ~/.config/atlassian-mcp
cp env.example ~/.config/atlassian-mcp/atlassian.env
chmod 600 ~/.config/atlassian-mcp/atlassian.env
$EDITOR ~/.config/atlassian-mcp/atlassian.env
```

Fill in the four values. See `env.example` for what each one is.

Verify before wiring anything up. For Server/Data Center the token goes in a
`Bearer` header, not basic auth:

```bash
set -a; . ~/.config/atlassian-mcp/atlassian.env; set +a

curl -s -H "Authorization: Bearer $JIRA_PERSONAL_TOKEN" \
  "$JIRA_URL/rest/api/2/myself" | head -c 200

curl -s -H "Authorization: Bearer $CONFLUENCE_PERSONAL_TOKEN" \
  "$CONFLUENCE_URL/rest/api/user/current" | head -c 200
```

## 2. Point the MCP client at it

### Claude Code / `.mcp.json`

Drop this into `.mcp.json` at the root of the project you start the agent from.

```json
{
  "mcpServers": {
    "jira": {
      "command": "uvx",
      "args": [
        "mcp-atlassian",
        "--env-file",
        "/Users/you/.config/atlassian-mcp/atlassian.env"
      ],
      "env": {
        "TOOLSETS": "default,jira_agile,jira_links,jira_worklog",
        "READ_ONLY_MODE": "false"
      }
    }
  }
}
```

Then approve the server once. Project-scope config is not trusted by default;
on Claude Code the approval lands in `.claude/settings.local.json`, which is
already gitignored:

```json
{ "enabledMcpjsonServers": ["jira"] }
```

### Codex / `.codex/config.toml`

```toml
[mcp_servers.jira]
command = "uvx"
args = [
    "mcp-atlassian",
    "--env-file",
    "/Users/you/.config/atlassian-mcp/atlassian.env",
]

[mcp_servers.jira.env]
TOOLSETS = "default,jira_agile,jira_links,jira_worklog"
READ_ONLY_MODE = "false"
```

`default` already carries Confluence pages and comments, so this value covers
both products at 50 tools. Naming a Confluence toolset is optional, not
required.

## 3. Confirm it is actually up

In the agent, ask it to read one issue. A server that started but is
unauthenticated still lists tools — it only fails on the first real call, so
exercise a call, not just the connection.

To check which tools a given `TOOLSETS` value really produces, without an
agent and without credentials:

```bash
python3 tools/probe_toolsets.py
```

Measured against `mcp-atlassian` 0.23.1 (Atlassian MCP 3.4.8):

| `TOOLSETS` | tools |
|---|---|
| unset or `all` | 98 |
| `default` | 35 |
| `default,jira_agile,jira_links,jira_worklog` | 50 |
| `confluence_pages` | 14 |
| `confluence` (not a real name) | **0** |
| `jira_issue` (typo) | **0** |

## Toolset names

Toolset names are granular per product area. There is no single `confluence`
or `jira` toolset — those tokens are silently ignored.

| Jira (16) | Confluence (8) |
|---|---|
| `jira_issues` ⭐ | `confluence_pages` ⭐ |
| `jira_fields` ⭐ | `confluence_comments` ⭐ |
| `jira_comments` ⭐ | `confluence_labels` |
| `jira_transitions` ⭐ | `confluence_users` |
| `jira_projects` | `confluence_analytics` |
| `jira_agile` | `confluence_attachments` |
| `jira_links` | `confluence_templates` |
| `jira_worklog` | `confluence_permissions` |
| `jira_attachments` | |
| `jira_users` | |
| `jira_watchers` | |
| `jira_service_desk` | |
| `jira_forms` | |
| `jira_metrics` | |
| `jira_development` | |
| `jira_project_analysis` | |

⭐ = part of `default` (6 toolsets, 35 tools). `all` means all 24, 98 tools.
Because Confluence pages and comments are in `default`, Confluence works
without naming any Confluence toolset — which is exactly why a wrong
Confluence token in `TOOLSETS` goes unnoticed.

## Options worth knowing

| Variable | Effect |
|---|---|
| `TOOLSETS` | Which tool groups are exposed. Unset means all. See the table above for real names. |
| `READ_ONLY_MODE` | `true` removes every write tool. Start here. Flip to `false` only when the agent should be able to comment, transition or create. |
| `JIRA_SSL_VERIFY` / `CONFLUENCE_SSL_VERIFY` | Set to `false` only for an instance with a self-signed certificate. Prefer installing the CA properly. |

## Gotchas

- **A wrong toolset name is fail-closed and silent.** Unknown names are dropped
  with a log warning, and if nothing valid is left the server exposes **zero**
  tools while still connecting successfully. `TOOLSETS="confluence"` — the
  obvious guess — produces exactly that. Run `tools/probe_toolsets.py` after
  changing the value.
- **Jira and Confluence need separate tokens.** They are separate Atlassian
  applications with separate PATs. A Jira token against the Confluence API
  returns `401`, which reads like a wrong password and is not one.
- **Project scope is path-bound.** A `.mcp.json` or `.codex/config.toml` in a
  project directory is read only when the agent is started *from that
  directory*. Started one level up, the MCP server silently does not exist.
- **Trust is granted per path, not per repo.** Moving the project means
  approving the server again.
- **A missing env file is not fatal.** The server starts and exposes tools; the
  failure appears later as an authentication error on the first real call.
- **The env file wins over the ambient shell.** The server calls
  `load_dotenv(env_file, override=True)`, so a stale `JIRA_PERSONAL_TOKEN`
  exported in the terminal does *not* shadow the file. If you still get a
  `401`, the problem is in the file, not in the shell.

## Layout

```
.
├── env.example              # credential template, safe to commit
├── mcp.json.example         # Claude Code server entry
├── codex-config.toml.example # Codex server entry
└── .gitignore               # keeps any real *.env out of git
```

## License

MIT
