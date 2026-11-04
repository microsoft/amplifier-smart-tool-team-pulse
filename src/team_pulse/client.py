# SPDX-License-Identifier: MIT
"""TeamPulseClient -- async HTTP client for the Team Pulse API.

Config is read from the environment and ~/.team-pulse/.env (see team_pulse.config).

Construction validation, pooled httpx.AsyncClient lifecycle, and eager
credential acquisition at context-manager entry.

Usage::

    async with TeamPulseClient(base_url=..., auth=...) as client:
        # _http is active, credential already validated
        ...
"""

from __future__ import annotations

import json
from typing import Any, Literal, Self

import httpx

from team_pulse.auth import AzCredentialAuth
from team_pulse.config import Config
from team_pulse.errors import (
    TeamPulseAPIError,
    TeamPulseAuthError,
    TeamPulseConnectionError,
)
from team_pulse.models import AnswerUpload, ClientInfo, Question

# ---------------------------------------------------------------------------
# Module-level sentinels
# ---------------------------------------------------------------------------

_NO_CONTEXT_MSG: str = (
    "TeamPulseClient must be used inside 'async with client:' before making requests"
)

# Sentinel for detecting "auth_mode not explicitly passed" in __init__.
# Using object() avoids any accidental truthiness match.
_UNSET: object = object()


def _server_error_message(body: str) -> str:
    """Extract the server's ``{"error": {"message": ...}}`` detail, if present.

    Returns a `" Server said: <message>"` suffix so an auth failure surfaces the
    server's *actionable* message (e.g. ``bearer_required`` on the bulk corpus
    endpoint: "a shared API key cannot attribute a bulk pull to a specific
    member") instead of only the generic client-side hint. Returns "" when the
    body is absent or not the structured-error shape.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return ""
    err = parsed.get("error") if isinstance(parsed, dict) else None
    msg = err.get("message") if isinstance(err, dict) else None
    return f" Server said: {msg}" if isinstance(msg, str) and msg else ""


# ---------------------------------------------------------------------------
# TeamPulseClient
# ---------------------------------------------------------------------------


class TeamPulseClient:
    """Async HTTP client for the Team Pulse API.

    Must be used as an async context manager. The credential is validated
    **eagerly** at ``__aenter__`` -- never mid-request -- so failures surface
    at a clean boundary and are always wrapped as :exc:`TeamPulseAuthError`.

    Args:
        base_url: Team Pulse server URL. Required; raises :exc:`ValueError`
            if falsy. Trailing slashes are stripped.
        auth: Authentication strategy satisfying the :class:`AuthStrategy`
            protocol (``async def headers() -> dict[str, str]``).
        timeout: HTTP timeout in seconds (default ``60.0``).
        auth_mode: Provenance field -- which strategy *auth* implements.
            Defaults to ``'key'``.
        api_app_id: Provenance field -- Azure AD app ID used when ``auth_mode='az'``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        auth: Any,
        timeout: float = 60.0,
        auth_mode: Any = _UNSET,  # Literal["key", "az"] when supplied explicitly
        api_app_id: str | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self._base_url: str = base_url.rstrip("/")
        self._auth: Any = auth
        self._timeout: float = timeout

        # Provenance -- type-based inference when auth_mode is not explicitly supplied.
        # This is TYPE INSPECTION ONLY: no env reads, no IO, no auth inference policy.
        # Factories (connect / from_env / from_config) always supply auth_mode from
        # Config, so inference only fires for direct __init__ construction.
        if auth_mode is _UNSET:
            auth_mode = "az" if isinstance(auth, AzCredentialAuth) else "key"
        self._auth_mode: Literal["key", "az"] = auth_mode  # type: ignore[assignment]

        # Infer api_app_id from AzCredentialAuth instance when not explicitly passed.
        # Only triggered for direct __init__ construction (factories supply it from RC).
        if api_app_id is None and isinstance(auth, AzCredentialAuth):
            api_app_id = auth.api_app_id
        self._api_app_id: str | None = api_app_id

        # Lifecycle state
        self._http: httpx.AsyncClient | None = None
        self._resolved: bool = False
        self._auth_closed: bool = False  # guard against double-close

    # ------------------------------------------------------------------
    # Factory classmethods
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        timeout: float | None = None,
    ) -> TeamPulseClient:
        """Build a client from an explicit :class:`~team_pulse.config.Config`.

        Nothing ambient is read: the caller supplies every value. This is the
        entry point for a library caller who does not want to configure this
        tool through the process environment.

        Args:
            config: The settings to use. Which auth strategy runs follows
                from whether it carries a key.
            timeout: HTTP timeout in seconds. ``None`` uses the one on
                *config*, which is where `TEAM_PULSE_TIMEOUT` lands.
        """
        auth, mode = config.build_auth()
        return cls(
            base_url=config.require_url(),
            auth=auth,
            timeout=config.timeout if timeout is None else timeout,
            auth_mode=mode,
            api_app_id=config.api_app_id,
        )

    @classmethod
    def connect(
        cls,
        *,
        base_url: str | None = None,
        key: str | None = None,
        timeout: float | None = None,
    ) -> TeamPulseClient:
        """Construct from the environment, with optional explicit overrides.

        Precedence, high to low: the arguments here, then
        :meth:`Config.from_env` (the ``.env`` file, then the environment, then
        the shipped defaults).

        Args:
            base_url: Server URL. ``None`` falls through to the environment.
            key: API key. ``None`` falls through to the environment.
            timeout: HTTP timeout in seconds. ``None`` falls through too.
        """
        config = Config.from_env().with_overrides(url=base_url, key=key)
        return cls.from_config(config, timeout=timeout)

    @classmethod
    def from_env(cls, *, timeout: float | None = None) -> TeamPulseClient:
        """Construct purely from the environment and the config file.

        ``from_env`` intentionally has no ``base_url`` parameter: a factory of
        that name accepting an in-code URL would be a naming lie. Use
        :meth:`connect` or :meth:`from_config` to supply values in code.
        """
        return cls.connect(timeout=timeout)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def _credential_type(self) -> str:
        """Return the provenance label for the credential strategy in use."""
        return "azure_default_credential" if self._auth_mode == "az" else "api_key"

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        http = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        try:
            # Eagerly acquire credential at context boundary -- never mid-request.
            # Uniform: never branches on auth strategy; always calls headers().
            await self._auth.headers()
        except TeamPulseAuthError:
            await http.aclose()
            self._http = None
            await (
                self._close_auth()
            )  # release credential resources (e.g. AzCredentialAuth aiohttp session)
            raise
        except Exception as exc:
            await http.aclose()
            self._http = None
            await (
                self._close_auth()
            )  # release credential resources on unexpected failure
            raise TeamPulseAuthError(
                "Could not acquire a Team Pulse credential. "
                "For Azure, run `az login` or set AZURE_CLIENT_ID / a managed identity; "
                "for key auth, set TEAM_PULSE_KEY. "
                f"Underlying error: {exc}"
            ) from exc
        self._http = http
        self._resolved = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        await self._close_auth()

    async def _close_auth(self) -> None:
        """Close the auth strategy if it exposes a ``close()`` method.

        Idempotent -- safe to call from both ``__aexit__`` (success path) and
        ``__aenter__`` failure handlers (so a failed credential acquisition never
        leaks an open ``aiohttp.ClientSession`` inside DefaultAzureCredential).
        """
        if self._auth_closed:
            return
        self._auth_closed = True
        close_fn = getattr(self._auth, "close", None)
        if callable(close_fn):
            try:
                await close_fn()  # type: ignore[misc]
            except Exception:  # noqa: BLE001, S110 -- see below
                # Deliberate: this runs while an exception may already be
                # propagating. Raising here would replace the caller's real
                # error with a teardown error. No logger exists in this
                # library, so there is nothing to log to.
                pass

    # ------------------------------------------------------------------
    # Internal guards
    # ------------------------------------------------------------------

    def _require_http(self) -> httpx.AsyncClient:
        """Return the active HTTP client, or raise :exc:`RuntimeError` if outside context.

        Raises:
            RuntimeError: When called outside an ``async with`` block.
        """
        if self._http is None:
            raise RuntimeError(_NO_CONTEXT_MSG)
        return self._http

    # ------------------------------------------------------------------
    # HTTP helpers -- typed error mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _best_effort_body(resp: httpx.Response) -> str:
        """Return the response body as text, or empty string if decoding fails."""
        try:
            return resp.text
        except Exception:  # noqa: BLE001 -- body decode must never mask the API error
            return ""

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Make an authenticated request, mapping failures to the typed exception family.

        Raises:
            TeamPulseConnectionError: Transport-level failure (DNS, refused, timeout).
            TeamPulseAuthError: HTTP 401 or 403 from the server.
            TeamPulseAPIError: Any other non-2xx HTTP response.
        """
        http = self._require_http()
        headers = {"Accept": "application/json", **await self._auth.headers()}
        try:
            resp = await http.request(method, path, headers=headers, **kwargs)
        except httpx.TransportError as exc:
            raise TeamPulseConnectionError(
                f"Could not reach Team Pulse at {self._base_url}{path}: {exc}"
            ) from exc
        if resp.status_code in (401, 403):
            server_msg = _server_error_message(self._best_effort_body(resp))
            raise TeamPulseAuthError(
                f"Team Pulse rejected the credential (HTTP {resp.status_code}) for {path}. "
                f"Check the API key or Azure identity authorization.{server_msg}"
            )
        if resp.status_code >= 400:
            raise TeamPulseAPIError(
                status=resp.status_code, body=self._best_effort_body(resp)
            )
        return resp

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Issue an authenticated GET request."""
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, json_body: Any) -> httpx.Response:
        """Issue an authenticated POST request with a JSON body."""
        return await self._request("POST", path, json=json_body)

    # ------------------------------------------------------------------
    # Envelope normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_list_envelope(body: Any) -> dict[str, Any]:
        """Return a guaranteed {count: int, resources: list} dict.

        Non-dict body yields the empty envelope. Missing or wrong-typed
        ``resources`` / ``count`` fields are fixed up in-place so callers
        always receive a well-formed envelope.
        """
        if not isinstance(body, dict):
            return {"count": 0, "resources": []}
        resources = body.get("resources")
        if not isinstance(resources, list):
            resources = []
        count = body.get("count")
        if not isinstance(count, int):
            count = len(resources)
        return {**body, "count": count, "resources": resources}

    # ------------------------------------------------------------------
    # Generic lens reads
    # ------------------------------------------------------------------

    async def describe(self) -> ClientInfo:
        """Return a provenance-only snapshot of the resolved client configuration.

        **Never makes a network call; never exposes secrets.** Safe to call at
        any point -- before or after entering the async context manager.
        """
        return ClientInfo(
            base_url=self._base_url,
            auth_mode=self._auth_mode,
            api_app_id=self._api_app_id,
            credential_type=self._credential_type,  # type: ignore[arg-type]
            resolved=self._resolved,
            az_identity_hint=(
                self._auth.az_identity_hint
                if isinstance(self._auth, AzCredentialAuth)
                else None
            ),
        )

    async def info(self) -> Any:
        """GET /api/lens/info -- return the server's self-description."""
        resp = await self._get("/api/lens/info")
        return resp.json()

    async def whoami(self) -> Any:
        """GET /api/lens/me -- server-verified caller identity, where supported."""
        resp = await self._get("/api/lens/me")
        return resp.json()

    async def resources(
        self,
        type: str | None = None,
        *,
        collection: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """GET /api/lens/resources -- list resources, optionally filtered.

        Args:
            type: Resource type filter (e.g. ``'project'``). Omitted when falsy.
            collection: Collection name from ``info``.
            status: Question status filter (active, archived, all).
            limit: Record count limit; omitted means the existing unbounded list.
            offset: Record offset, not a document byte offset.
        """
        params: dict[str, Any] = {"offset": offset}
        if type:
            params["type"] = type
        for name, value in (
            ("collection", collection),
            ("status", status),
            ("limit", limit),
        ):
            if value is not None:
                params[name] = value
        resp = await self._get("/api/lens/resources", params=params or None)
        return self._normalize_list_envelope(resp.json())

    async def get(
        self,
        id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        expected_revision: str | None = None,
    ) -> Any:
        """GET /api/lens/resources/{id} -- fetch one resource or document page.

        Args:
            id: Full resource ID (e.g. ``'members/devis'``).
                Leading slashes are stripped so both forms are accepted.
            offset: UTF-8 byte offset for documents (default 0).
            limit: Content byte budget; None uses the server default (32768,
                maximum 153600). This does not cap the JSON envelope size.
            expected_revision: Pin the full served text revision. Required for
                nonzero document offsets; a mismatch is HTTP 409 source_changed.

        Returns the full server envelope, including content_revision and paging
        metadata. Entities remain structured and nonpaged. Makes exactly one
        request; never assembles pages or retries without a revision.

        Raises:
            ValueError: When *id* is falsy.
        """
        if not id:
            raise ValueError("id is required")
        path = f"/api/lens/resources/{id.lstrip('/')}"
        params: dict[str, Any] = {"offset": offset}
        if limit is not None:
            params["limit"] = limit
        if expected_revision is not None:
            params["expected_revision"] = expected_revision
        resp = await self._get(path, params=params)
        return resp.json()

    async def search(
        self,
        q: str,
        limit: int = 50,
        *,
        collection: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/lens/resources/search -- search resources by query string.

        Args:
            q: Search query. Required; raises :exc:`ValueError` if falsy.
            limit: Max results (default ``50``).
            collection: Optional collection name from ``info``. Search has no
                offset parameter. Hit snippets are not full documents.

        Raises:
            ValueError: When *q* is falsy.
        """
        if not q:
            raise ValueError("q is required")
        params: dict[str, Any] = {"q": q, "limit": limit}
        if collection is not None:
            params["collection"] = collection
        resp = await self._get("/api/lens/resources/search", params=params)
        return self._normalize_list_envelope(resp.json())

    async def prefix(
        self, prefix: str, *, limit: int | None = None, offset: int = 0
    ) -> dict[str, Any]:
        """GET /api/lens/resources/prefix/{prefix} -- list resources by ID prefix.

        Args:
            prefix: ID prefix (e.g. ``'projects'``). Required; raises
                :exc:`ValueError` if falsy. Leading slashes are stripped.
            limit: Record count limit; omitted means the existing unbounded list.
            offset: Record offset, not a document byte offset.

        Raises:
            ValueError: When *prefix* is falsy.
        """
        if not prefix:
            raise ValueError("prefix is required")
        path = f"/api/lens/resources/prefix/{prefix.lstrip('/')}"
        params: dict[str, Any] = {"offset": offset}
        if limit is not None:
            params["limit"] = limit
        resp = await self._get(path, params=params)
        return self._normalize_list_envelope(resp.json())

    async def ask(self, prompt: str, focus: str | None = None) -> Any:
        """POST /api/lens/ask -- ask the Team Pulse LLM a question.

        Args:
            prompt: The question to ask. Required.
            focus: Optional lens resource ID used as orientation hint.
                Omitted from the request body when ``None``.

        Returns:
            Raw dict with ``content``, ``prompt_used``, and ``provenance``.
        """
        body: dict[str, Any] = {"prompt": prompt}
        if focus is not None:
            body["focus"] = focus
        resp = await self._post("/api/lens/ask", json_body=body)
        return resp.json()

    # ------------------------------------------------------------------
    # Bulk corpus download
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Typed question reads
    # ------------------------------------------------------------------

    @staticmethod
    def _question_from_resource(resource: Any) -> Question:
        """Convert a raw resource envelope dict into a :class:`Question` dataclass.

        Handles missing or malformed data defensively: a non-dict resource is
        treated as an empty dict, and a missing ``lookback_days`` field returns
        ``None`` without raising ``KeyError``.
        """
        if not isinstance(resource, dict):
            resource = {}
        data = resource.get("data")
        if not isinstance(data, dict):
            data = {}
        qid = data.get("id")
        if not qid:
            envelope_id = str(resource.get("id", ""))
            qid = envelope_id.split("/", 1)[-1] if envelope_id else ""
        return Question(
            question_id=str(qid),
            question=str(data.get("question", "")),
            lookback_days=data.get("lookback_days"),
        )

    async def fetch_questions(self, status: str = "active") -> list[Question]:
        """GET /api/lens/resources?type=question&status=<status> -- typed :class:`Question`.

        The ``status`` filter is sent to the server, which applies it
        authoritatively (archived questions are only returned for
        ``'archived'``/``'all'``). It is ALSO re-applied client-side as a
        defensive fallback, so an older server that ignores the query param still
        yields the correct set for the default ``'active'`` case.

        Args:
            status: One of ``'active'`` (default), ``'archived'``, or ``'all'``.
                ``'all'`` returns every question regardless of status.
        """
        resp = await self._get(
            "/api/lens/resources", params={"type": "question", "status": status}
        )
        body = resp.json()
        resources = body.get("resources") if isinstance(body, dict) else None
        if not isinstance(resources, list):
            resources = []
        result: list[Question] = []
        for res in resources:
            if not isinstance(res, dict):
                continue
            data = res.get("data")
            if not isinstance(data, dict):
                data = {}
            res_status = data.get("status", "active")
            if status != "all" and res_status != status:
                continue
            result.append(self._question_from_resource(res))
        return result

    async def fetch_question(self, question_id: str) -> Question:
        """GET /api/lens/resources/questions/{question_id} -- fetch a single question.

        Args:
            question_id: Bare question slug (e.g. ``'higher-level-work'``).
        """
        resp = await self._get(f"/api/lens/resources/questions/{question_id}")
        return self._question_from_resource(resp.json())

    # ------------------------------------------------------------------
    # Answer submission
    # ------------------------------------------------------------------

    async def upload_answer(self, answer: AnswerUpload) -> Any:
        """POST /api/lens/answers -- submit an AI-synthesised answer.

        Sends the canonical wire body::

            {question_id, user_id, generated_at, answer, metadata}

        ``metadata`` is ALWAYS present (an empty dict when the caller supplied
        none); session provenance lives **inside** it.

        Returns the server's reply verbatim -- the persisted record, including
        ``respondent_handle``, the canonical handle it resolved ``user_id`` to.
        Nothing is picked out or dropped: a caller confirming that the right
        person was attributed needs the record the server actually wrote.
        """
        body: dict[str, Any] = {
            "question_id": answer.question_id,
            "user_id": answer.user_id,
            "generated_at": answer.generated_at,
            "answer": answer.answer,
            "metadata": answer.metadata,
        }

        resp = await self._post("/api/lens/answers", json_body=body)
        return resp.json() if resp.content else {}
