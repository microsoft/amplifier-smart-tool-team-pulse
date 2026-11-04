"""Deterministic capabilities: thin sync wrappers over the async client.

Credentials are read at CALL time via `team_pulse.config.from_env()` (invoked
indirectly through `TeamPulseClient.from_env()`), never at import time --
`import team_pulse` succeeds with a completely empty environment.

Document windows are server-defined. Every get makes one request and preserves
the returned revision and continuation metadata; no automatic page assembly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from team_pulse import config as tpl_config
from team_pulse.client import TeamPulseClient
from team_pulse.config import Config
from team_pulse.models import AnswerUpload


class TeamPulseNotConfigured(RuntimeError):
    """No team-pulse endpoint/credentials are resolvable.

    The underlying config layer signals this with a bare ``ValueError``, which
    reaches a caller with no actionable remedy. Every capability that needs the
    network converts it here instead, so an agent caller is told exactly what to
    set rather than being handed a stack trace.
    """

    def __init__(self, detail: str = "") -> None:
        self.remedy = (
            "Set the team-pulse endpoint and credentials, then retry.\n"
            "  1. Set the endpoint:  export TEAM_PULSE_URL=https://<your-endpoint>\n"
            "  2. Authenticate, either:\n"
            "       a. Azure AD (recommended): run `az login` -- no key to mint or store; or\n"
            "       b. API key:  export TEAM_PULSE_KEY=<key>\n"
            "  3. Or persist the endpoint once:  team-pulse configure --url <url>\n"
            "     (writes ~/.team-pulse/.env; relocate with TEAM_PULSE_DIR)\n"
            "These may also be set in a .env file in the working directory "
            "(relocate with TEAM_PULSE_DIR). See CONFIGURATION.md."
        )
        suffix = f" ({detail})" if detail else ""
        super().__init__(f"team-pulse is not configured{suffix}.\n{self.remedy}")


def _call(
    fn: Callable[[TeamPulseClient], Awaitable[Any]],
    config: Config | None = None,
) -> Any:
    """Run *fn* against a freshly opened `TeamPulseClient`.

    *config* is the settings to use. ``None`` means "read the environment",
    which is what the CLI wants; a library caller passes a `Config` and nothing
    ambient is consulted.
    """
    cfg = config if config is not None else Config.from_env()

    async def _inner() -> Any:
        async with TeamPulseClient.from_config(cfg) as client:
            return await fn(client)

    try:
        return asyncio.run(_inner())
    except ValueError as exc:
        # The vendored config layer raises a bare ValueError when the endpoint
        # or credentials are absent. Convert it once, here, so every capability
        # fails with a remedy rather than leaking an unactionable error.
        raise TeamPulseNotConfigured(str(exc)) from exc


def info(*, config: Config | None = None) -> Any:
    """Describe the SERVER: name/version, resource_types, collections, capabilities.

    Raises the same typed exceptions as every other read (`TeamPulseAuthError`,
    `TeamPulseAPIError`, `TeamPulseConnectionError`) when the network or
    credentials are not usable.
    """
    return _call(lambda client: client.info(), config)


def whoami(*, config: Config | None = None) -> Any:
    """Read the caller's server-verified identity from /api/lens/me, if supported."""
    return _call(lambda client: client.whoami(), config)


def search(
    q: str,
    limit: int = 50,
    *,
    collection: str | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Substring search across resources. Default limit 50, server max 200.

    `limit` is validated here rather than left to the server, which answers an
    out-of-range value with an HTTP 422 -- a round trip spent learning
    something the caller could have been told immediately.
    """
    if not 1 <= limit <= 200:
        raise ValueError(f"limit must be between 1 and 200, got {limit}")
    return _call(
        lambda client: client.search(q, limit=limit, collection=collection), config
    )


def prefix(
    prefix: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    config: Config | None = None,
) -> dict[str, Any]:
    """List an ID branch. Optional limit/offset count records; default is unbounded."""
    return _call(
        lambda client: client.prefix(prefix, limit=limit, offset=offset), config
    )


def get(
    id: str,
    *,
    offset: int = 0,
    limit: int | None = None,
    expected_revision: str | None = None,
    config: Config | None = None,
) -> Any:
    """Fetch one resource or document page by exact ID.

    Document offset/limit measure UTF-8 content bytes, not serialized JSON.
    None uses the server's 153600-byte default (maximum 153600). For a needed
    continuation use metadata.next_offset and content_revision as expected_revision.
    Nonzero document offsets require a revision. HTTP 409 source_changed and
    invalid-argument HTTP 422 remain errors: no unpinned retry or automatic pages.
    Structured entity responses are unchanged and nonpaged.
    """
    return _call(
        lambda client: client.get(
            id, offset=offset, limit=limit, expected_revision=expected_revision
        ),
        config,
    )


def resources(
    type: str | None = None,
    *,
    collection: str | None = None,
    status: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    config: Config | None = None,
) -> dict[str, Any]:
    """List resources, optionally filtered by type, collection or question status.

    Optional limit/offset count records; the default remains unbounded.
    `type` is commonly `member` or `question`, but that is a hint rather than
    the contract -- this tool holds no list of its own, because the server's
    would go stale in it. `info()` reports the authoritative `resource_types`,
    and an unknown value returns a 400 naming the valid ones.
    """
    return _call(
        lambda client: client.resources(
            type=type, collection=collection, status=status, limit=limit, offset=offset
        ),
        config,
    )


def status(*, config: Config | None = None) -> dict[str, Any]:
    """Is this tool working, and with what settings?

    Reports the resolved configuration and, when it is configured, whether the
    server actually answers. No secrets are included.

    NEVER raises, and works with a completely empty environment: describing a
    broken setup is this verb's job, so every failure becomes a report rather
    than an exception. The guards are deliberately broad -- config resolution
    can fail with more than one exception type (a missing URL raises
    ValueError, a malformed key raises TeamPulseAuthError), and catching them
    individually is how this verb stopped being total the first time.
    """
    from team_pulse import __version__

    result: dict[str, Any] = {"tool": "team-pulse", "version": __version__}
    try:
        cfg = config if config is not None else Config.from_env()
        client = TeamPulseClient.from_config(cfg)
        info_snapshot = asyncio.run(client.describe())
        conf: dict[str, Any] = {"configured": True, **asdict(info_snapshot)}
    except Exception as exc:  # noqa: BLE001 -- status must never raise
        conf = {"configured": False, "error": str(exc)}
    result["config"] = conf

    if not conf.get("configured"):
        result["reachability"] = {
            "checked": False,
            "reason": "not configured; see CONFIGURATION.md",
        }
        return result
    try:
        _call(lambda client: client.info(), config)
        result["reachability"] = {"checked": True, "ok": True}
    except Exception as exc:  # noqa: BLE001 -- status must never raise
        result["reachability"] = {
            "checked": True,
            "ok": False,
            "error": str(exc),
            "remedy": getattr(exc, "remedy", ""),
        }
    return result


def ask_service(
    prompt: str, focus: str | None = None, *, config: Config | None = None
) -> Any:
    """Ask the Team Pulse SERVER's own LLM a question.

    Spends the SERVER's model budget, not yours -- distinct from
    `team_pulse.ask_local.ask_local()`, which uses your own provider.
    """
    return _call(lambda client: client.ask(prompt, focus=focus), config)


def submit_answer(
    user_id: str,
    question_id: str,
    answer: str,
    generated_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """Record a session-mined answer to a reflection question for a github user.

    WRITE -- this reaches shared team data, attributed to `user_id`. There is
    no confirmation flag: a flag cannot tell who set it, so it enforced
    nothing while implying it did. The bundle had none either. What guards
    this call is the capability description, which tells an agent to put the
    write to the user before making it.

    `generated_at` defaults to the current UTC time when omitted.
    """
    ts = generated_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    upload = AnswerUpload(
        question_id=question_id,
        user_id=user_id,
        answer=answer,
        generated_at=ts,
        metadata=metadata or {},
    )
    return _call(lambda client: client.upload_answer(upload), config)


def configure(
    url: str,
    client_id: str | None = None,
    *,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist the team-pulse endpoint URL (and optional Azure AD app id).

    Writes your own settings file -- the local, reversible equivalent of
    exporting `TEAM_PULSE_URL`. Not fenced: refusing to save an endpoint the
    caller just supplied would only make first-run setup harder.

    *path* is where to write. ``None`` means ``~/.team-pulse/.env`` (or
    ``$TEAM_PULSE_DIR``), which is what the CLI wants. A library caller passes
    a path and no environment variable is consulted -- this verb writes a file,
    so the destination is the setting, and it is an argument like any other.
    """
    if not url or not url.startswith("https://"):
        raise ValueError("url must be a non-empty https:// URL")
    path = tpl_config.save_config(url, api_app_id=client_id, path=path)
    return {"saved_url": url, "persisted_path": str(path)}
