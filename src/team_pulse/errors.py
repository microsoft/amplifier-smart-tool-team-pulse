# SPDX-License-Identifier: MIT
"""Typed exception family for team_pulse.

This library RAISES these exceptions; callers catch them directly.

Auth failures carry an actionable message explaining what the caller must do
(e.g. set TEAM_PULSE_KEY, or ensure `az login` has been run).
"""

from __future__ import annotations

import json
from typing import Any, ClassVar


class TeamPulseError(Exception):
    """Base class for all team_pulse errors."""


class TeamPulseAuthError(TeamPulseError):
    """Credential acquisition failed, or the server returned HTTP 401/403."""


class TeamPulseAPIError(TeamPulseError):
    """Any non-2xx response that is not an auth failure.

    Carries the HTTP status code and the decoded response body so callers can
    inspect or log them without re-parsing the raw bytes.

    Also carries a `remedy`. The server names the valid values in its own error
    message, so for the errors where that is actionable the remedy says which
    capability to call rather than pointing at the docs. There is deliberately
    no client-side list of resource types: the server is authoritative and
    self-describing, and a hardcoded copy would go stale.

    `server` is the server's own `{code, message, status}` envelope, verbatim
    and structured, or ``None`` when the body was not that shape. It exists so
    a caller can branch on the server's `code` without parsing JSON back out of
    a message string -- the bundle passed that envelope straight through, and
    flattening it into prose made it unreachable.
    """

    #: Server error codes for which a specific remedy is worth stating.
    _REMEDIES: ClassVar[dict[str, str]] = {
        "unsupported_type": (
            "Call `info` for this server's resource_types -- the message above "
            "lists the valid values."
        ),
        "source_changed": (
            "The document changed. Do not retry automatically without a revision or "
            "combine these pages. Report the conflict; a deliberate new read starts at offset 0."
        ),
    }

    def __init__(self, *, status: int, body: str) -> None:
        self.status = status
        self.body = body
        self.server = self._server_envelope(body)
        self.remedy = self._REMEDIES.get(
            str((self.server or {}).get("code", "")), "See the tool documentation."
        )
        super().__init__(f"Team Pulse API error {status}: {body}")

    @staticmethod
    def _server_envelope(body: str) -> dict[str, Any] | None:
        """The server's own error object, or None if the body is not one.

        Never raises: a body that is HTML, empty, or JSON of another shape is
        simply not an envelope, and the caller still has `body` and `status`.
        """
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        err = parsed.get("error")
        return err if isinstance(err, dict) else None


class TeamPulseConnectionError(TeamPulseError):
    """Transport-level failure (DNS resolution, connection refused, timeout)."""
