"""Claude Code hook handlers for the dev dashboard activity feed. Stdlib-only.

``dd.py hook <event>`` reads the hook payload (JSON) from stdin and calls :func:`handle`. The handlers log only
``tool_input.description`` and ``subagent_type``; ``tool_input.command`` is never read, so secrets in commands
cannot reach the log. Nothing here imports the server, state or gate modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from devdash import ensure, events

EVENTS: Final[tuple[str, ...]] = ("session-start", "pre-tool", "subagent-stop")
"""Hook event names accepted by :func:`handle` and ``dd.py hook``."""

_DEFAULT_AGENT: Final = "agent"


def _text(value: object) -> str | None:
    """Return ``value`` when it is a non-blank string, else None."""
    return value if isinstance(value, str) and value.strip() else None


def _ident(value: object, default: str) -> str:
    """Return a payload identifier cleaned like a message (redacted, control characters flattened), cut to 64."""
    text = _text(value)
    return events.redact(" ".join(text.split()))[:64] if text is not None else default


def _session(payload: Mapping[str, Any]) -> str | None:
    """Return the payload's ``session_id`` when it is a non-blank string."""
    return _text(payload.get("session_id"))


def _pre_tool(payload: Mapping[str, Any], dev: Path) -> None:
    """Log an Agent/Task start or a Bash description. Other tools, and Bash without a description, log nothing."""
    tool = payload.get("tool_name")
    raw = payload.get("tool_input")
    tool_input: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    description = _text(tool_input.get("description"))
    session = _session(payload)
    if tool in ("Agent", "Task"):
        agent = _ident(tool_input.get("subagent_type"), _DEFAULT_AGENT)
        events.emit(agent, "start", description or "agent started", ref=f"agent:{agent}", session=session, dev=dev)
    elif tool == "Bash" and description is not None:
        events.emit("claude", "info", description, session=session, dev=dev)


def _subagent_stop(payload: Mapping[str, Any], dev: Path) -> None:
    """Log that a subagent finished."""
    # UNVERIFIED (A-hook-1): the SubagentStop payload field name; M0 would capture a real payload and check it.
    agent = _ident(payload.get("agent_type") or payload.get("subagent_type"), _DEFAULT_AGENT)
    events.emit(agent, "done", "agent finished", ref=f"agent:{agent}", session=_session(payload), dev=dev)


def _session_start(payload: Mapping[str, Any], dev: Path, repo: Path) -> str:
    """Start the dashboard server if needed, log the session start and return the line shown as session context."""
    try:
        url = ensure.ensure_running(repo, dev, ensure.default_port())
    except Exception as exc:  # a hook must never fail the session; any startup error becomes text
        reason = events.redact(" ".join(str(exc).split()))[:160]
        return f"SAE dashboard: not started ({reason})"
    events.emit("claude", "info", "session started", session=_session(payload), dev=dev)
    return f"SAE dashboard: {url}"


def handle(event: str, payload: Mapping[str, Any], dev: Path, repo: Path) -> str:
    """Handle one hook ``event`` and return the text for stdout (empty for most events).

    Unknown events return an empty string. Event emission never raises (see :func:`devdash.events.emit`).
    """
    if event == "session-start":
        return _session_start(payload, dev, repo)
    if event == "pre-tool":
        _pre_tool(payload, dev)
    elif event == "subagent-stop":
        _subagent_stop(payload, dev)
    return ""
