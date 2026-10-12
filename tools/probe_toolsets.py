"""Report how many MCP tools mcp-atlassian exposes for a given TOOLSETS value.

Speaks JSON-RPC over stdio directly, so it needs no MCP client, no agent and no
valid credentials: `initialize` and `tools/list` are answered before any
Atlassian API call is made. This matters because a bad toolset name fails
closed and silently — the server connects, reports no error, and exposes zero
tools.

Usage:
    python3 tools/probe_toolsets.py                      # built-in cases
    python3 tools/probe_toolsets.py default,jira_agile   # ad-hoc values
    python3 tools/probe_toolsets.py ''   all   default   # unset, all, default

Requires `uvx` on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# Placeholders only. The server validates credentials lazily, on the first real
# Atlassian call, which this probe never makes.
ENV_TEMPLATE = (
    "JIRA_URL=https://jira.invalid\n"
    "JIRA_PERSONAL_TOKEN=probe-not-a-real-token\n"
    "CONFLUENCE_URL=https://confluence.invalid\n"
    "CONFLUENCE_PERSONAL_TOKEN=probe-not-a-real-token\n"
)

DEFAULT_CASES = [
    ("unset", ""),
    ("all", "all"),
    ("default", "default"),
    ("default + jira_agile/jira_links/jira_worklog", "default,jira_agile,jira_links,jira_worklog"),
    ("confluence (not a real name)", "confluence"),
    ("confluence_pages", "confluence_pages"),
    ("typo name", "jira_issue"),
]

PROTOCOL_VERSION = "2024-11-05"
INITIALIZE_TIMEOUT_S = 180
RESPONSE_TIMEOUT_S = 180


def cleanup(proc: subprocess.Popen[str], env_path: str) -> None:
    """Close the pipes, stop the server and remove the temporary env file."""
    if proc.stdin is not None and not proc.stdin.closed:
        try:
            proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    Path(env_path).unlink(missing_ok=True)


def report_startup_failure(env_path: str, stderr_lines: list[str]) -> None:
    """Explain why the server never answered, instead of failing silently."""
    print(f"  server did not answer initialize (env file: {env_path})")
    if not stderr_lines:
        print("  stderr was empty — check that `uvx` exists and can reach the network")
        return
    for line in stderr_lines[-20:]:
        print(f"  | {line}")


def count_tools(toolsets: str) -> int | None:
    """Return the tool count for one TOOLSETS value, or None if no reply came."""
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
        handle.write(ENV_TEMPLATE)
        env_path = handle.name

    env = dict(os.environ)
    env.pop("TOOLSETS", None)
    if toolsets:
        env["TOOLSETS"] = toolsets

    proc = subprocess.Popen(
        ["uvx", "mcp-atlassian", "--env-file", env_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    assert proc.stdin is not None, "no stdin pipe"
    assert proc.stdout is not None, "no stdout pipe"
    assert proc.stderr is not None, "no stderr pipe"

    # stderr is drained continuously. It carries the server banner, and it is
    # the only place the reason for a failed startup appears.
    stderr_lines: list[str] = []

    def drain_stderr() -> None:
        for line in proc.stderr:
            stderr_lines.append(line.rstrip("\n"))
            del stderr_lines[:-200]

    threading.Thread(target=drain_stderr, daemon=True).start()

    # stdin must stay open until both replies arrive: closing it early races the
    # server shutdown and tools/list is lost with no error.
    responses: dict[int, dict[str, object]] = {}
    lock = threading.Lock()
    both_received = threading.Event()

    def reader() -> None:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            message_id = message.get("id")
            if message_id not in (1, 2):
                continue
            with lock:
                responses[message_id] = message
            if len(responses) == 2:
                both_received.set()
                return

    threading.Thread(target=reader, daemon=True).start()

    def send(payload: dict[str, object]) -> bool:
        """Write one request. False means the server already died."""
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            return False
        return True

    send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "probe_toolsets", "version": "1.0"},
        },
    })
    for _ in range(INITIALIZE_TIMEOUT_S * 10):
        if 1 in responses:
            break
        both_received.wait(0.1)

    if 1 not in responses:
        report_startup_failure(env_path, stderr_lines)
        cleanup(proc, env_path)
        return None

    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    both_received.wait(RESPONSE_TIMEOUT_S)

    cleanup(proc, env_path)

    message = responses.get(2)
    if message is None:
        return None
    result = message.get("result")
    if not isinstance(result, dict):
        return None
    tools = result.get("tools")
    if not isinstance(tools, list):
        return None
    return len(tools)


def main(argv: list[str]) -> int:
    if argv:
        cases = [(f"TOOLSETS={value!r}" if value else "unset", value) for value in argv]
    else:
        cases = DEFAULT_CASES

    failures = 0
    for label, value in cases:
        count = count_tools(value)
        if count is None:
            print(f"{label:46} no reply from server")
            failures += 1
            continue
        verdict = ""
        if count == 0:
            verdict = "   <-- fail-closed: no tools, and no error is reported"
        print(f"{label:46} tools={count}{verdict}")

    print()
    print("Zero tools with a successful connection means at least one toolset")
    print("name does not exist. Real names are listed in README.md.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
