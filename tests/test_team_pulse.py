"""Acceptance tests for the team-pulse smart tool distribution. No network access.

Deterministic capabilities are exercised against a `_FakeClient` monkeypatched
in place of `team_pulse.api.TeamPulseClient`, so these tests never touch the
network or require real credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import os as _os
from pathlib import Path
from typing import Any, ClassVar, Self

import httpx
import pytest

import team_pulse as tp
from team_pulse import Config, api, model

# Import the module object explicitly so monkeypatching its globals works
# regardless of what `team_pulse/__init__.py` re-exports from it. The name
# below is the MODULE, not the
# module. Go through sys.modules via importlib to get the real submodule.
answer_mod = importlib.import_module("team_pulse.ask_local")

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stand-in for TeamPulseClient. Records calls; returns canned data."""

    calls: ClassVar[list[tuple[str, tuple[Any, ...], dict[str, Any]]]] = []
    get_return: ClassVar[Any] = {"id": "projects/x", "data": {}}
    info_return: ClassVar[Any] = {"resource_types": [], "collections": []}

    def __init__(self, **kwargs: Any) -> None:
        # status() constructs TeamPulseClient(base_url=..., auth=..., ...)
        # directly (not via from_env()) -- accept and ignore those kwargs.
        self._kwargs = kwargs

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> _FakeClient:
        return cls()

    @classmethod
    def from_env(cls, *, timeout: float = 60.0) -> _FakeClient:
        return cls()

    async def describe(self) -> Any:
        from team_pulse.models import ClientInfo

        return ClientInfo(
            base_url=self._kwargs.get("base_url", "https://team-pulse.example.com"),
            auth_mode=self._kwargs.get("auth_mode", "key"),
            api_app_id=self._kwargs.get("api_app_id"),
            credential_type="api_key",
            resolved=False,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def info(self) -> Any:
        type(self).calls.append(("info", (), {}))
        return type(self).info_return

    async def whoami(self) -> Any:
        type(self).calls.append(("whoami", (), {}))
        return {"member": {"handle": "jdoe"}}

    async def search(
        self, q: str, limit: int = 50, *, collection: str | None = None
    ) -> Any:
        type(self).calls.append(
            ("search", (q,), {"limit": limit, "collection": collection})
        )
        return {"count": 0, "resources": []}

    async def prefix(
        self, prefix: str, *, limit: int | None = None, offset: int = 0
    ) -> Any:
        type(self).calls.append(
            ("prefix", (prefix,), {"limit": limit, "offset": offset})
        )
        return {"count": 0, "resources": []}

    async def get(
        self,
        id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        expected_revision: str | None = None,
    ) -> Any:
        type(self).calls.append(
            (
                "get",
                (id,),
                {
                    "offset": offset,
                    "limit": limit,
                    "expected_revision": expected_revision,
                },
            )
        )
        return type(self).get_return

    async def resources(
        self,
        type: str | None = None,
        *,
        collection: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Any:
        _FakeClient.calls.append(
            (
                "resources",
                (),
                {
                    "type": type,
                    "collection": collection,
                    "status": status,
                    "limit": limit,
                    "offset": offset,
                },
            )
        )
        return {"count": 0, "resources": []}

    async def ask(self, prompt: str, focus: str | None = None) -> Any:
        type(self).calls.append(("ask", (prompt,), {"focus": focus}))
        return {"content": "..."}

    async def upload_answer(self, answer: Any) -> Any:
        type(self).calls.append(("upload_answer", (answer,), {}))
        # The server's reply, nested under "answer" as the real one is.
        return {
            "answer": {
                "id": "abc",
                "question_id": answer.question_id,
                "respondent_handle": "devis",
                "answer": answer.answer,
                "source": "session-mining",
            }
        }


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test starts with no team-pulse or provider credentials configured."""
    for key in (
        "TEAM_PULSE_URL",
        "TEAM_PULSE_KEY",
        "TEAM_PULSE_API_APP_ID",
        "TEAM_PULSE_DIR",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    # Saved settings outrank environment variables. Keep these no-network tests
    # away from the real user's endpoint/credentials as well as their env.
    monkeypatch.setenv("TEAM_PULSE_DIR", str(tmp_path))
    _FakeClient.calls = []
    _FakeClient.get_return = {"id": "projects/x", "data": {}}


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    monkeypatch.setattr(api, "TeamPulseClient", _FakeClient)
    monkeypatch.setenv("TEAM_PULSE_URL", "https://team-pulse.example.com")
    monkeypatch.setenv("TEAM_PULSE_KEY", "tp_test_key")
    return _FakeClient


# ---------------------------------------------------------------------------
# Import and module-level contract
# ---------------------------------------------------------------------------


def test_module_imports_with_empty_environment() -> None:
    assert callable(tp.info)
    assert callable(tp.whoami)
    assert callable(tp.search)
    assert callable(tp.prefix)
    assert callable(tp.get)
    assert callable(tp.resources)
    assert callable(tp.status)
    assert callable(tp.ask_service)
    assert callable(tp.submit_answer)
    assert callable(tp.configure)
    assert callable(tp.ask_local)
    assert callable(tp.manifest)
    assert callable(tp.capabilities)


def test_capabilities_lists_twelve_entries_with_flags() -> None:
    # Includes the explicit caller-identity read.
    caps = {c.name: (c.destructive, c.model_backed) for c in tp.capabilities()}
    assert len(caps) == 12
    assert caps["submit_answer"] == (True, False)
    # `configure` is NOT destructive: it merges into the caller's own settings
    # file on their own machine. `destructive` means the write reaches shared
    # data other people can see.
    assert caps["configure"] == (False, False)
    assert caps["ask_local"] == (False, True)


def test_manifest_matches_pyproject_version() -> None:
    m = tp.manifest()
    assert m.name == "team-pulse"
    assert m.smart_tool_format == 1
    assert m.version == tp.__version__ == "0.1.0"
    assert len(m.requires) == 3


# ---------------------------------------------------------------------------
# prompts.py
# ---------------------------------------------------------------------------


def test_retrieval_strategy_loads_and_is_nonempty() -> None:
    from team_pulse.prompts import retrieval_strategy

    text = retrieval_strategy()
    assert isinstance(text, str)
    assert len(text) > 100
    assert "team-pulse-expert" in text


def test_system_prompt_carries_both_documents_verbatim() -> None:
    """The bundle gave its agent an instruction AND a data-model reference.

    Both reach the model: porting only the instruction left it citing a
    reference it could not read.
    """
    from team_pulse.prompts import data_model_reference, retrieval_strategy

    root = Path(__file__).resolve().parents[1]
    strategy = (root / "prompts" / "retrieval-strategy.md").read_text(encoding="utf-8")

    system = retrieval_strategy()
    assert strategy.rstrip() in system
    assert data_model_reference() in system
    assert "Resource taxonomy" in system
    assert "Common query patterns" in system


def test_data_model_reference_is_the_manifest_section() -> None:
    """One copy. It lives in the manifest body so a reader learning how to
    call the tool and the model driving `ask_local` get the same text."""
    import team_pulse as tp
    from team_pulse.prompts import data_model_reference

    body = tp.manifest().body
    assert "## Data model reference" in body
    assert data_model_reference() in body


def test_manifest_reference_code_fences_are_intact() -> None:
    """Pins a real defect: a blanket backtick replace during the port turned
    every ```python fence into ``python, wrecking the examples."""
    import team_pulse as tp

    for line in tp.manifest().body.splitlines():
        if line.startswith("``"):
            assert line.startswith("```"), f"broken fence: {line!r}"


def test_system_prompt_has_no_unresolvable_bundle_mention() -> None:
    """`@team-pulse:...` is bundle syntax that resolves to nothing here."""
    from team_pulse.prompts import retrieval_strategy

    assert "@team-pulse:" not in retrieval_strategy()


# ---------------------------------------------------------------------------
# Request shapes (deterministic capabilities)
# ---------------------------------------------------------------------------


def test_info_request_shape(fake_client: type[_FakeClient]) -> None:
    result = tp.info()
    assert result == fake_client.info_return
    assert fake_client.calls == [("info", (), {})]


def test_search_request_shape(fake_client: type[_FakeClient]) -> None:
    tp.search("onboarding", limit=10)
    assert fake_client.calls == [
        ("search", ("onboarding",), {"limit": 10, "collection": None})
    ]


def test_prefix_request_shape(fake_client: type[_FakeClient]) -> None:
    tp.prefix("projects")
    assert fake_client.calls == [
        ("prefix", ("projects",), {"limit": None, "offset": 0})
    ]


def test_resources_request_shape(fake_client: type[_FakeClient]) -> None:
    tp.resources(type="question")
    assert fake_client.calls == [
        (
            "resources",
            (),
            {
                "type": "question",
                "collection": None,
                "status": None,
                "limit": None,
                "offset": 0,
            },
        )
    ]


def test_search_rejects_a_limit_the_server_would_422(
    fake_client: type[_FakeClient],
) -> None:
    """Validated here rather than spending a round trip to learn it."""
    for bad in (0, -1, 201, 500):
        with pytest.raises(ValueError, match="between 1 and 200"):
            tp.search("q", limit=bad)
    assert fake_client.calls == [], "no request should reach the client"


def test_ask_request_shape(fake_client: type[_FakeClient]) -> None:
    tp.ask_service("hello", focus="projects/x")
    assert fake_client.calls == [
        ("ask", ("hello",), {"focus": "projects/x"}),
    ]


def test_submit_answer_confirmed_request_shape(fake_client: type[_FakeClient]) -> None:
    result = tp.submit_answer(
        "jdoe",
        "higher-level-work",
        "did the thing",
        generated_at="2026-01-01T00:00:00Z",
    )
    # The server's record comes back whole -- notably respondent_handle, the
    # canonical handle it resolved user_id to.
    record = result["answer"]
    assert record["id"] == "abc"
    assert record["question_id"] == "higher-level-work"
    assert record["respondent_handle"] == "devis"
    assert record["answer"] == "did the thing"
    assert record["source"] == "session-mining"
    assert fake_client.calls[0][0] == "upload_answer"
    upload = fake_client.calls[0][1][0]
    assert upload.question_id == "higher-level-work"
    assert upload.user_id == "jdoe"


# ---------------------------------------------------------------------------
# Mechanism 1: get() size cap
# ---------------------------------------------------------------------------


def test_get_under_cap_returns_normally(fake_client: type[_FakeClient]) -> None:
    fake_client.get_return = {"id": "projects/x", "data": {"title": "small"}}
    result = tp.get("projects/x")
    assert result == fake_client.get_return


# ---------------------------------------------------------------------------
# The write guard is advisory, so the advisory has to be there
# ---------------------------------------------------------------------------


def test_destructive_capabilities_warn_and_say_nothing_can_stop_them() -> None:
    """There is no confirmation flag -- a flag cannot tell who set it.

    The description is the entire guard, so it must say what the call does and
    that the caller is the one responsible. If this test fails, a write is
    reaching a model with no warning attached.
    """
    from team_pulse.catalog import CATALOG

    destructive = [c for c in CATALOG if c.destructive]
    assert destructive, "expected at least one destructive capability"
    for cap in destructive:
        assert "WRITES TO SHARED" in cap.description.upper()
        assert "approval" in cap.description or "approve" in cap.description


def test_submit_answer_takes_no_confirmation_argument() -> None:
    """Pins the removal.

    The flag was security theatre: the model loop passed it automatically, so
    the one caller most at risk never met it, and any other caller wanting to
    write simply set it. The bundle had no such flag.
    """
    import inspect

    from team_pulse.catalog import BY_VERB

    assert "confirmed" not in inspect.signature(tp.submit_answer).parameters
    assert "confirmed" not in [a.name for a in BY_VERB["submit_answer"].arguments]


def test_api_error_remedy_points_at_info_for_unsupported_type() -> None:
    """The server names the valid values; the remedy says where to look them up.

    Deliberately no client-side list of resource types -- the server is
    authoritative and self-describing, so a hardcoded copy would go stale.
    """
    from team_pulse.errors import TeamPulseAPIError

    body = (
        '{"error":{"code":"unsupported_type","message":'
        "\"Unknown resource type: 'bogus'. Valid types: ['member', 'question']\","
        '"status":400}}'
    )
    exc = TeamPulseAPIError(status=400, body=body)
    assert "info" in exc.remedy


def test_api_error_nests_the_server_envelope_verbatim() -> None:
    """The server's own {code, message, status}, structured -- not re-parsed
    out of a message string. The bundle passed this envelope straight through.
    """
    from team_pulse.errors import TeamPulseAPIError

    body = (
        '{"error":{"code":"unsupported_type","message":'
        '"Unknown resource type: \'bogus\'.","status":400}}'
    )
    exc = TeamPulseAPIError(status=400, body=body)
    assert exc.server == {
        "code": "unsupported_type",
        "message": "Unknown resource type: 'bogus'.",
        "status": 400,
    }


@pytest.mark.parametrize(
    "body", ["<html>502</html>", "", "[1,2,3]", '{"detail":"x"}', "null"]
)
def test_api_error_server_is_none_when_the_body_is_not_an_envelope(
    body: str,
) -> None:
    """Never raises while parsing: the caller still has `body` and `status`."""
    from team_pulse.errors import TeamPulseAPIError

    exc = TeamPulseAPIError(status=500, body=body)
    assert exc.server is None
    assert exc.remedy


def test_api_error_remedy_falls_back_for_other_codes() -> None:
    from team_pulse.errors import TeamPulseAPIError

    other = TeamPulseAPIError(
        status=404, body='{"error":{"code":"not_found","message":"nope","status":404}}'
    )
    assert other.remedy == "See the tool documentation."
    # A body that is not JSON at all must not raise while building the remedy.
    assert TeamPulseAPIError(status=500, body="<html>502</html>").remedy


def test_timeout_is_configurable_and_reaches_the_client() -> None:
    from team_pulse.client import TeamPulseClient

    cfg = Config(url="https://x.example.com", key="tp_aaaaaaaaaa", timeout=75.0)
    assert TeamPulseClient.from_config(cfg)._timeout == 75.0
    # An explicit argument still wins over the configured value.
    assert TeamPulseClient.from_config(cfg, timeout=5.0)._timeout == 5.0
    # And the default when nothing says otherwise.
    plain = Config(url="https://x.example.com", key="tp_aaaaaaaaaa")
    assert TeamPulseClient.from_config(plain)._timeout == 60.0


@pytest.mark.parametrize("bad", ["soon", "-5", "0"])
def test_timeout_fails_loud_on_garbage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad: str
) -> None:
    monkeypatch.setenv("TEAM_PULSE_DIR", str(tmp_path))
    monkeypatch.setenv("TEAM_PULSE_TIMEOUT", bad)
    with pytest.raises(ValueError, match="TEAM_PULSE_TIMEOUT"):
        Config.from_env()


def test_configure_writes_without_confirmation(tmp_path: Path) -> None:
    # Not fenced: it saves settings the caller just supplied, to their own file.
    dest = tmp_path / "my.env"
    result = tp.configure("https://team-pulse.example.com", path=dest)
    assert result["persisted_path"] == str(dest)
    assert "TEAM_PULSE_URL=https://team-pulse.example.com" in dest.read_text()


def test_configure_rejects_a_non_https_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        tp.configure("http://insecure.example.com", path=tmp_path / "x.env")


# ---------------------------------------------------------------------------
# status() -- never raises, works with an empty environment
# ---------------------------------------------------------------------------


def test_status_reports_unconfigured_on_empty_env() -> None:
    result = tp.status()
    assert result["config"]["configured"] is False
    assert "error" in result["config"]
    assert result["reachability"]["checked"] is False


def test_status_never_raises_on_empty_environment() -> None:
    result = tp.status()
    assert result["tool"] == "team-pulse"
    assert result["config"]["configured"] is False
    assert result["reachability"]["checked"] is False


def test_status_probes_reachability_when_configured(
    fake_client: type[_FakeClient],
) -> None:
    result = tp.status()
    assert result["config"]["configured"] is True
    assert result["reachability"] == {"checked": True, "ok": True}


# ---------------------------------------------------------------------------
# answer() -- provider precondition, no-citation-no-answer, hallucination guard
# ---------------------------------------------------------------------------


def test_answer_raises_no_provider_configured_on_empty_env() -> None:
    with pytest.raises(model.NoProviderConfigured) as excinfo:
        tp.ask_local("What is team-pulse?")
    assert excinfo.value.remedy
    assert (
        "OPENAI_API_KEY" in excinfo.value.remedy
        or "ANTHROPIC_API_KEY" in excinfo.value.remedy
    )


def _turn(*calls: tuple[str, dict[str, Any]], text: str | None = None) -> Any:
    """Build a ModelTurn as the provider would return it."""
    from team_pulse.model import ModelTurn, ToolCall

    return ModelTurn(
        text=text,
        tool_calls=tuple(
            ToolCall(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ),
    )


def _scripted(*turns: Any) -> Any:
    """A converse() stand-in that replays *turns* in order."""
    seq = iter(turns)

    def fake_converse(**kwargs: Any) -> Any:
        return next(seq)

    return fake_converse


def test_answer_returns_none_when_model_gives_no_citations(
    monkeypatch: pytest.MonkeyPatch, fake_client: type[_FakeClient]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(answer_mod, "info", lambda **kw: {})
    monkeypatch.setattr(
        answer_mod,
        "converse",
        _scripted(_turn(("answer", {"answer": "some answer", "citations": []}))),
    )
    result = tp.ask_local("What is team-pulse?")
    assert result.answer == "some answer"
    assert result.citations == []


def test_answer_returns_citations_as_the_model_gave_them(
    monkeypatch: pytest.MonkeyPatch, fake_client: type[_FakeClient]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(answer_mod, "info", lambda **kw: {})
    monkeypatch.setattr(
        answer_mod,
        "converse",
        _scripted(
            _turn(
                (
                    "answer",
                    {"answer": "some answer", "citations": ["projects/never-fetched"]},
                )
            )
        ),
    )
    result = tp.ask_local("What is team-pulse?")
    assert result.answer == "some answer"
    # The bundle did not validate citations, so neither does this.
    assert result.citations == ["projects/never-fetched"]


def test_answer_cites_a_resource_actually_fetched(
    monkeypatch: pytest.MonkeyPatch, fake_client: type[_FakeClient]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(answer_mod, "info", lambda **kw: {})
    fake_client.get_return = {"id": "projects/x", "data": {}}
    monkeypatch.setattr(
        answer_mod,
        "converse",
        _scripted(
            _turn(("get", {"id": "projects/x"})),
            _turn(("answer", {"answer": "yes", "citations": ["projects/x"]})),
        ),
    )
    result = tp.ask_local("What is team-pulse?")
    assert result.answer == "yes"
    assert result.citations == ["projects/x"]


def test_answer_runs_as_many_steps_as_the_model_wants(
    monkeypatch: pytest.MonkeyPatch, fake_client: type[_FakeClient]
) -> None:
    """No step budget: a model that searches 12 times still gets to answer.

    The old loop capped this at 6 and returned nothing.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(answer_mod, "info", lambda **kw: {})
    fake_client.get_return = {"id": "projects/x", "data": {}}
    turns = [_turn(("search", {"q": f"q{i}"})) for i in range(12)]
    turns.append(_turn(("get", {"id": "projects/x"})))
    turns.append(_turn(("answer", {"answer": "found it", "citations": ["projects/x"]})))
    monkeypatch.setattr(answer_mod, "converse", _scripted(*turns))

    result = tp.ask_local("What is team-pulse?")
    assert result.answer == "found it"


def test_answer_returns_prose_when_model_stops_without_calling_answer(
    monkeypatch: pytest.MonkeyPatch, fake_client: type[_FakeClient]
) -> None:
    """Ending in prose is how the model finishes a turn. It is an answer."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(answer_mod, "info", lambda **kw: {})
    monkeypatch.setattr(answer_mod, "converse", _scripted(_turn(text="I give up.")))
    result = tp.ask_local("What is team-pulse?")
    assert result.answer == "I give up.", "prose is an answer, as it was in the bundle"
    assert result.citations == []


def test_answer_feeds_retrieval_errors_back_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, fake_client: type[_FakeClient]
) -> None:
    """A failing get() becomes a tool result the model can react to."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(answer_mod, "info", lambda **kw: {})

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("resource exploded")

    monkeypatch.setattr(answer_mod, "get", boom)
    monkeypatch.setattr(
        answer_mod,
        "converse",
        _scripted(
            _turn(("get", {"id": "projects/x"})),
            _turn(("answer", {"answer": "no", "citations": []})),
        ),
    )
    result = tp.ask_local("What is team-pulse?")
    # The failed get is reported to the model as a tool result, not raised.
    assert result.answer == "no"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_short_and_long_help_exit_zero_and_list_verbs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from team_pulse.cli import main

    assert main(["-h"]) == 0
    short_out = capsys.readouterr().out
    assert main(["--help"]) == 0
    long_out = capsys.readouterr().out
    for verb in ("info", "search", "get", "ask-local", "status", "manifest"):
        assert verb in short_out
        assert verb in long_out


def test_cli_status_exits_zero_on_empty_env(capsys: pytest.CaptureFixture[str]) -> None:
    from team_pulse.cli import main

    assert main(["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "team-pulse"


def test_cli_unknown_verb_exits_nonzero_with_error_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from team_pulse.cli import main

    # argparse's own error() path raises SystemExit(1) directly rather than
    # returning through main().
    with pytest.raises(SystemExit) as excinfo:
        main(["not-a-real-verb"])
    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "UsageError"


def test_cli_credential_needing_verb_exits_one_on_empty_env(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from team_pulse.cli import main

    assert main(["info"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "error" in payload
    assert payload["error"]["remedy"]


# ---------------------------------------------------------------------------
# status() totality
#
# Both promise to report a broken setup rather than fail on one. The guard
# must be broad: config resolution raises ValueError for a missing URL but
# TeamPulseAuthError for a malformed key under a pinned auth mode, and
# catching only the first is how these verbs stopped being total before.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "label"),
    [
        ({}, "empty environment"),
        (
            {"TEAM_PULSE_URL": "https://example.com"},
            "url only, no credentials and no app id",
        ),
        (
            {
                "TEAM_PULSE_URL": "https://example.com",
                "TEAM_PULSE_KEY": "not-tp-prefixed",
            },
            "malformed key falls through to Azure, no app id",
        ),
    ],
)
def test_status_never_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str], label: str
) -> None:
    for key in list(_os.environ):
        if key.startswith("TEAM_PULSE_"):
            monkeypatch.delenv(key, raising=False)
    # Point the state dir at an empty tmp dir: the .env file outranks the
    # environment, so a real ~/.team-pulse/.env would otherwise leak in.
    monkeypatch.setenv("TEAM_PULSE_DIR", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    conf = api.status()
    assert isinstance(conf, dict), label
    assert conf["config"]["configured"] is False, label
    assert conf["config"].get("error"), f"{label}: a report must say what is wrong"

    result = api.status()
    assert isinstance(result, dict), label
    assert result["config"]["configured"] is False, label
    assert result["reachability"]["checked"] is False, label


# ---------------------------------------------------------------------------
# The get() size cap measures real UTF-8 bytes
#
# json.dumps defaults to ensure_ascii=True, which escapes non-ASCII to \uXXXX
# and inflates the measurement ~1.5x for non-Latin content -- refusing
# resources that are comfortably under the cap.
# ---------------------------------------------------------------------------


def test_answer_passes_through_every_citation_the_model_gave(
    monkeypatch: pytest.MonkeyPatch, fake_client: type[_FakeClient]
) -> None:
    """No filtering: the citation list is the model's, verbatim."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(answer_mod, "info", lambda **kw: {})
    fake_client.get_return = {"id": "projects/x", "data": {}}
    monkeypatch.setattr(
        answer_mod,
        "converse",
        _scripted(
            _turn(("get", {"id": "projects/x"})),
            _turn(
                (
                    "answer",
                    {"answer": "sourced", "citations": ["projects/x", "projects/y"]},
                )
            ),
        ),
    )
    result = tp.ask_local("What is team-pulse?")
    assert result.answer == "sourced"
    assert result.citations == ["projects/x", "projects/y"]


def test_answer_never_returns_none(
    monkeypatch: pytest.MonkeyPatch, fake_client: type[_FakeClient]
) -> None:
    """The bundle had no null outcome. Neither does this."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(answer_mod, "info", lambda **kw: {})
    for turns in (
        [_turn(text="just prose")],
        [_turn(("answer", {"answer": "no sources", "citations": []}))],
        [
            _turn(
                (
                    "answer",
                    {"answer": "bad source", "citations": ["never/fetched"]},
                )
            )
        ],
    ):
        monkeypatch.setattr(answer_mod, "converse", _scripted(*turns))
        assert tp.ask_local("q").answer is not None


# ---------------------------------------------------------------------------
# Lens paging: one request per page, full envelopes, errors stay errors.
# ---------------------------------------------------------------------------


def _document_page() -> dict[str, Any]:
    # Full captured normalized text, not JSON bytes or a hash of the window.
    full = "é🙂 evidence\n" * 4000
    revision = "lens-text-v1:" + hashlib.sha256(full.encode("utf-8")).hexdigest()
    content = full[:18000]
    returned = len(content.encode("utf-8"))
    return {
        "id": "corpus/repos/example.md",
        "type": "doc",
        "content": content,
        "content_revision": revision,
        "metadata": {
            "offset": 0,
            "returned_bytes": returned,
            "total_bytes": len(full.encode("utf-8")),
            "next_offset": returned,
            "truncated": True,
            "source_path": "fixture/example.md",
        },
    }


def _http_read(
    operation: str, payload: Any, *, response_status: int = 200, **kwargs: Any
) -> tuple[Any, list[httpx.Request]]:
    """Use the real authenticated HTTP path against an in-process transport."""
    from team_pulse.auth import ApiKeyAuth
    from team_pulse.client import TeamPulseClient

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(response_status, json=payload)

    async def run() -> Any:
        client = TeamPulseClient(
            base_url="https://fixture.invalid", auth=ApiKeyAuth("tp_test_key")
        )
        async with httpx.AsyncClient(
            base_url="https://fixture.invalid", transport=httpx.MockTransport(handle)
        ) as http:
            client._http = http
            return await getattr(client, operation)(**kwargs)

    return asyncio.run(run()), requests


def test_http_get_preserves_full_page_and_does_not_auto_continue() -> None:
    page = _document_page()
    result, requests = _http_read("get", page, id=page["id"])
    assert result == page
    assert len(requests) == 1
    assert dict(requests[0].url.params) == {"offset": "0"}
    assert len(json.dumps(result)) > 32768  # Content budget is not a JSON cap.
    assert (
        len(result["content"].encode("utf-8")) == result["metadata"]["returned_bytes"]
    )
    assert result["metadata"]["returned_bytes"] <= 32768
    assert result["metadata"]["truncated"] is True


def test_http_get_forwards_pinned_byte_window_exactly() -> None:
    page = _document_page()
    result, requests = _http_read(
        "get",
        page,
        id=page["id"],
        offset=32767,
        limit=153600,
        expected_revision=page["content_revision"],
    )
    assert result == page
    assert len(requests) == 1
    assert requests[0].url.path == "/api/lens/resources/corpus/repos/example.md"
    assert dict(requests[0].url.params) == {
        "offset": "32767",
        "limit": "153600",
        "expected_revision": page["content_revision"],
    }


@pytest.mark.parametrize("operation", ["resources", "search", "prefix"])
def test_http_lists_preserve_envelope_and_indexed_hit_revision(operation: str) -> None:
    payload = {
        "count": 1,
        "resources": [
            {
                "id": "corpus/repos/example.md",
                "title": "Example",
                "snippet": "partial",
                "content_revision": _document_page()["content_revision"],
            }
        ],
        "metadata": {"truncated": True, "next_offset": 11},
        "future_field": {"kept": True},
    }
    kwargs = {
        "resources": {
            "type": "question",
            "collection": "docs",
            "status": "all",
            "limit": 10,
            "offset": 1,
        },
        "search": {"q": "partial", "collection": "corpus", "limit": 10},
        "prefix": {"prefix": "corpus/repos", "limit": 10, "offset": 1},
    }[operation]
    result, requests = _http_read(operation, payload, **kwargs)
    assert result == payload
    assert len(requests) == 1
    expected = {k: str(v) for k, v in kwargs.items() if k != "prefix"}
    assert dict(requests[0].url.params) == expected


@pytest.mark.parametrize(
    "operation,kwargs",
    [
        ("resources", {}),
        ("prefix", {"prefix": "members"}),
    ],
)
def test_http_browse_default_stays_unbounded(
    operation: str, kwargs: dict[str, Any]
) -> None:
    result, requests = _http_read(operation, {"count": 0, "resources": []}, **kwargs)
    assert result["count"] == 0
    assert dict(requests[0].url.params) == {"offset": "0"}
    assert "limit" not in requests[0].url.params


def test_http_entity_remains_structured_and_whoami_is_server_read() -> None:
    entity = {"id": "members/jdoe", "data": {"handle": "jdoe"}, "metadata": {}}
    result, requests = _http_read("get", entity, id=entity["id"])
    assert result == entity
    assert len(requests) == 1
    identity, requests = _http_read("whoami", {"member": {"handle": "jdoe"}})
    assert identity == {"member": {"handle": "jdoe"}}
    assert requests[0].url.path == "/api/lens/me"


@pytest.mark.parametrize(
    "status,code,operation,kwargs",
    [
        (
            409,
            "source_changed",
            "get",
            {
                "id": "corpus/repos/example.md",
                "offset": 32768,
                "expected_revision": "lens-text-v1:" + "a" * 64,
            },
        ),
        (
            422,
            "invalid_argument",
            "get",
            {"id": "corpus/repos/example.md", "offset": 2},
        ),
        (
            422,
            "invalid_argument",
            "get",
            {"id": "corpus/repos/example.md", "limit": 153601},
        ),
        (503, "search_failed", "search", {"q": "partial"}),
    ],
)
def test_http_failures_remain_errors_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str,
    operation: str,
    kwargs: dict[str, Any],
) -> None:
    from team_pulse.client import TeamPulseClient

    # Track _request itself as well as the transport so any unpinned fallback
    # would be observable even when the first request raises.
    original = TeamPulseClient._request
    calls: list[Any] = []

    async def tracked(self: Any, *args: Any, **kw: Any) -> Any:
        calls.append((args, kw))
        return await original(self, *args, **kw)

    monkeypatch.setattr(TeamPulseClient, "_request", tracked)
    server = {"code": code, "message": "fixture failure", "status": status}
    with pytest.raises(tp.TeamPulseAPIError) as exc:
        _http_read(operation, {"error": server}, response_status=status, **kwargs)
    assert exc.value.status == status
    assert exc.value.server == server
    assert len(calls) == 1


def test_successful_zero_hit_search_is_not_error() -> None:
    empty = {"count": 0, "resources": [], "metadata": {"truncated": False}}
    assert _http_read("search", empty, q="absent")[0] == empty


def test_public_get_and_filters_forward_once(fake_client: type[_FakeClient]) -> None:
    page = _document_page()
    fake_client.get_return = page
    assert (
        tp.get(
            page["id"],
            offset=19,
            limit=32768,
            expected_revision=page["content_revision"],
        )
        == page
    )
    tp.resources("question", collection="docs", status="archived", limit=5, offset=2)
    tp.prefix("corpus/repos", limit=5, offset=2)
    tp.search("evidence", collection="corpus", limit=5)
    assert tp.whoami() == {"member": {"handle": "jdoe"}}
    assert fake_client.calls == [
        (
            "get",
            (page["id"],),
            {
                "offset": 19,
                "limit": 32768,
                "expected_revision": page["content_revision"],
            },
        ),
        (
            "resources",
            (),
            {
                "type": "question",
                "collection": "docs",
                "status": "archived",
                "limit": 5,
                "offset": 2,
            },
        ),
        ("prefix", ("corpus/repos",), {"limit": 5, "offset": 2}),
        ("search", ("evidence",), {"limit": 5, "collection": "corpus"}),
        ("whoami", (), {}),
    ]


def test_manifest_model_schemas_and_public_signatures_align() -> None:
    from team_pulse.catalog import BY_VERB, model_loop_tools
    from team_pulse.client import TeamPulseClient

    published = {c["name"]: c for c in tp.manifest().to_dict()["capabilities"]}
    for name in ("get", "resources", "search", "prefix", "whoami"):
        schema = published[name]["input_schema"]
        assert schema == BY_VERB[name].as_json_schema()
        params = set(inspect.signature(getattr(tp, name)).parameters) - {"config"}
        assert set(schema["properties"]) == params
        async_params = set(inspect.signature(getattr(TeamPulseClient, name)).parameters)
        assert async_params - {"self"} == params
        model_schema = next(
            c["input_schema"] for c in model_loop_tools() if c["name"] == name
        )
        assert model_schema == schema
    get_props = published["get"]["input_schema"]["properties"]
    assert get_props["offset"]["default"] == 0
    assert get_props["limit"]["maximum"] == 153600
    assert "default" not in get_props["limit"]  # Omitted uses server default.
    assert "offset" not in published["search"]["input_schema"]["properties"]
    assert "metadata" in published["submit_answer"]["input_schema"]["properties"]


@pytest.mark.parametrize(
    "argv,expected",
    [
        (
            [
                "get",
                "--id",
                "corpus/x.md",
                "--offset",
                "32768",
                "--limit",
                "1000",
                "--expected-revision",
                "lens-text-v1:abc",
            ],
            (
                "get",
                ("corpus/x.md",),
                {
                    "offset": 32768,
                    "limit": 1000,
                    "expected_revision": "lens-text-v1:abc",
                },
            ),
        ),
        (
            [
                "resources",
                "--type",
                "question",
                "--collection",
                "docs",
                "--status",
                "all",
                "--limit",
                "10",
                "--offset",
                "2",
            ],
            (
                "resources",
                (),
                {
                    "type": "question",
                    "collection": "docs",
                    "status": "all",
                    "limit": 10,
                    "offset": 2,
                },
            ),
        ),
        (
            ["search", "--q", "evidence", "--collection", "corpus", "--limit", "10"],
            ("search", ("evidence",), {"collection": "corpus", "limit": 10}),
        ),
        (
            ["prefix", "--prefix", "corpus", "--limit", "10", "--offset", "2"],
            ("prefix", ("corpus",), {"limit": 10, "offset": 2}),
        ),
        (["whoami"], ("whoami", (), {})),
    ],
)
def test_cli_paging_and_filters_forward(
    fake_client: type[_FakeClient],
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    expected: Any,
) -> None:
    from team_pulse.cli import main

    fake_client.get_return = _document_page()
    assert main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert fake_client.calls == [expected]
    if argv[0] == "get":
        assert payload == fake_client.get_return


@pytest.mark.parametrize(
    "status,code,argv,fn_name",
    [
        (
            409,
            "source_changed",
            [
                "get",
                "--id",
                "corpus/x.md",
                "--offset",
                "2",
                "--expected-revision",
                "lens-text-v1:old",
            ],
            "get",
        ),
        (
            422,
            "invalid_argument",
            ["get", "--id", "corpus/x.md", "--offset", "2"],
            "get",
        ),
        (503, "search_failed", ["search", "--q", "evidence"], "search"),
    ],
)
def test_cli_server_error_stays_error_no_retry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: int,
    code: str,
    argv: list[str],
    fn_name: str,
) -> None:
    from team_pulse.cli import main

    calls = []
    server = {"code": code, "message": "fixture failure", "status": status}

    def fail(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise tp.TeamPulseAPIError(status=status, body=json.dumps({"error": server}))

    monkeypatch.setattr(tp, fn_name, fail)
    assert main(argv) == 1
    assert json.loads(capsys.readouterr().out)["error"]["server"] == server
    assert len(calls) == 1


def test_model_loop_receives_whole_page_and_forwards_needed_window(
    monkeypatch: pytest.MonkeyPatch,
    fake_client: type[_FakeClient],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    page = _document_page()
    fake_client.get_return = page
    requested = {
        "id": page["id"],
        "offset": 32768,
        "limit": 153600,
        "expected_revision": page["content_revision"],
    }
    observed = []
    turns = iter(
        [
            _turn(("get", requested)),
            _turn(("answer", {"answer": "evidence", "citations": [page["id"]]})),
        ]
    )

    def converse(**kwargs: Any) -> Any:
        if kwargs["messages"][-1]["role"] == "tool":
            observed.append(json.loads(kwargs["messages"][-1]["content"]))
        return next(turns)

    monkeypatch.setattr(answer_mod, "converse", converse)
    assert tp.ask_local("question").citations == [page["id"]]
    assert observed == [page]  # Valid JSON beyond 8000 chars with all metadata.
    assert fake_client.calls == [
        ("get", (page["id"],), {k: v for k, v in requested.items() if k != "id"})
    ]


def test_model_loop_forwards_discovery_filters_and_identity(
    monkeypatch: pytest.MonkeyPatch,
    fake_client: type[_FakeClient],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(
        answer_mod,
        "converse",
        _scripted(
            _turn(("search", {"q": "evidence", "collection": "corpus", "limit": 5})),
            _turn(
                (
                    "resources",
                    {
                        "type": "question",
                        "collection": "docs",
                        "status": "all",
                        "limit": 5,
                        "offset": 2,
                    },
                )
            ),
            _turn(("prefix", {"prefix": "corpus", "limit": 5, "offset": 2})),
            _turn(("whoami", {})),
            _turn(("answer", {"answer": "done", "citations": []})),
        ),
    )
    tp.ask_local("question")
    assert fake_client.calls == [
        ("search", ("evidence",), {"collection": "corpus", "limit": 5}),
        (
            "resources",
            (),
            {
                "type": "question",
                "collection": "docs",
                "status": "all",
                "limit": 5,
                "offset": 2,
            },
        ),
        ("prefix", ("corpus",), {"limit": 5, "offset": 2}),
        ("whoami", (), {}),
    ]


@pytest.mark.parametrize(
    "status,code,operation,arguments",
    [
        (
            409,
            "source_changed",
            "get",
            {
                "id": "corpus/x.md",
                "offset": 32768,
                "expected_revision": "lens-text-v1:old",
            },
        ),
        (422, "invalid_argument", "get", {"id": "corpus/x.md", "offset": 2}),
        (503, "search_failed", "search", {"q": "evidence"}),
    ],
)
def test_model_loop_preserves_structured_errors_no_automatic_retry(
    monkeypatch: pytest.MonkeyPatch,
    fake_client: type[_FakeClient],
    status: int,
    code: str,
    operation: str,
    arguments: dict[str, Any],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    server = {"code": code, "message": "fixture failure", "status": status}
    calls, observed = [], []
    turns = iter(
        [
            _turn((operation, arguments)),
            _turn(("answer", {"answer": "error reported", "citations": []})),
        ]
    )

    def fail(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise tp.TeamPulseAPIError(status=status, body=json.dumps({"error": server}))

    def converse(**kwargs: Any) -> Any:
        if kwargs["messages"][-1]["role"] == "tool":
            observed.append(json.loads(kwargs["messages"][-1]["content"]))
        return next(turns)

    monkeypatch.setattr(answer_mod, operation, fail)
    monkeypatch.setattr(answer_mod, "converse", converse)
    assert tp.ask_local("question").answer == "error reported"
    assert len(calls) == 1
    assert observed[0]["server"] == server
    assert "error" in observed[0]
