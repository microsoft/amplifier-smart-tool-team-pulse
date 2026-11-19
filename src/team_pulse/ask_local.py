"""Model-backed `answer()` capability -- the retrieval loop over the client.

The retrieval-strategy prose (`prompts/retrieval-strategy.md`, shipped
verbatim) is used directly as the system prompt for a native tool-calling
loop this distribution owns.

The model decides when to stop, exactly as it did when this ran inside an
agent runtime: it keeps calling `search`/`get` until it calls `answer`. There
is no step budget. Tool names and arguments are validated by the provider
against the schemas below, so an unparseable response or an unknown action is
not a state this loop can reach -- which is what a step budget was previously
compensating for.

`answer()` returns what the model produced: its answer, and the citations it
gave. The model is trusted to cite, as it was when this ran inside an agent
runtime -- this loop adds no validation of its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from team_pulse.api import (
    TeamPulseNotConfigured,
    get,
    info,
    prefix,
    resources,
    search,
    submit_answer,
    whoami,
)
from team_pulse.catalog import model_loop_tools
from team_pulse.config import Config
from team_pulse.model import converse, ensure_provider
from team_pulse.prompts import retrieval_strategy

#: Derived from the catalog -- the single description of every capability.
_TOOLS: list[dict[str, Any]] = model_loop_tools()


@dataclass(frozen=True)
class Answer:
    """What `ask_local()` produced: the model's answer and the sources it cited.

    `citations` are the resource ids the model gave. It may be empty -- the
    model can answer without citing anything.
    """

    answer: str
    citations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"answer": self.answer, "citations": list(self.citations)}


def _prose(text: str | None) -> Answer:
    """The model finished in prose rather than by calling `answer`."""
    return Answer(answer=text or "", citations=[])


def ask_local(question: str, *, config: Config | None = None) -> Answer:
    """Answer `question` by retrieving from the Team Pulse corpus. MODEL-BACKED.

    The model drives the retrieval with native tool calls and decides when it
    has enough to answer. There is no step limit: the loop ends when the model
    calls the `answer` tool, or stops calling tools at all.

    Both preconditions are checked FIRST, before any model call, so a caller
    who is not set up never pays for a model turn that cannot possibly succeed.
    Nothing else is fetched up front -- the model calls `info` itself if it
    needs to know what the API exposes, as the bundle's agent did.

    Raises:
        NoProviderConfigured: no `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` is set.
        TeamPulseNotConfigured: no team-pulse endpoint/credentials resolve. A
            caller that can reach a human -- a CLI, or an agent talking to a
            user -- can ask for the URL and call `configure` before retrying.
    """
    cfg = config if config is not None else Config.from_env()
    ensure_provider("answer", cfg)
    # Fail before the first model call, not after it. Without this the model
    # runs, every tool call fails, and it writes a well-formed Answer whose
    # text is a configuration complaint -- which reads as success to a caller.
    try:
        cfg.build_auth()
    except ValueError as exc:
        raise TeamPulseNotConfigured(str(exc)) from exc
    system = retrieval_strategy()
    opening = [f"Question: {question}"]
    messages: list[dict[str, Any]] = [{"role": "user", "content": "\n".join(opening)}]

    while True:
        turn = converse(
            capability="answer",
            config=cfg,
            system=system,
            messages=messages,
            tools=_TOOLS,
        )

        if not turn.tool_calls:
            # The model finished in prose, which is how it ends a turn when it
            # has said what it has to say. That is an answer; it simply carries
            # no citations of its own.
            return _prose(turn.text)

        messages.append(
            {
                "role": "assistant",
                "text": turn.text,
                "tool_calls": list(turn.tool_calls),
            }
        )

        finish: Answer | None = None
        for call in turn.tool_calls:
            if call.name == "answer":
                finish = _finish(call)
                result: Any = {"ok": True}
            elif call.name == "search":
                result = _run(
                    search,
                    str(call.arguments.get("q", "")),
                    limit=int(call.arguments.get("limit", 50)),
                    collection=call.arguments.get("collection"),
                    config=cfg,
                )
            elif call.name == "get":
                result = _run(
                    get,
                    str(call.arguments.get("id", "")),
                    offset=call.arguments.get("offset", 0),
                    limit=call.arguments.get("limit"),
                    expected_revision=call.arguments.get("expected_revision"),
                    config=cfg,
                )
            elif call.name == "info":
                result = _run(info, config=cfg)
            elif call.name == "whoami":
                result = _run(whoami, config=cfg)
            elif call.name == "prefix":
                result = _run(
                    prefix,
                    str(call.arguments.get("prefix", "")),
                    limit=call.arguments.get("limit"),
                    offset=call.arguments.get("offset", 0),
                    config=cfg,
                )
            elif call.name == "resources":
                result = _run(
                    resources,
                    type=call.arguments.get("type"),
                    collection=call.arguments.get("collection"),
                    status=call.arguments.get("status"),
                    limit=call.arguments.get("limit"),
                    offset=call.arguments.get("offset", 0),
                    config=cfg,
                )
            elif call.name == "submit_answer":
                result = _run(
                    submit_answer,
                    str(call.arguments.get("user_id", "")),
                    str(call.arguments.get("question_id", "")),
                    str(call.arguments.get("answer", "")),
                    generated_at=call.arguments.get("generated_at"),
                    config=cfg,
                )
            else:  # pragma: no cover -- the provider constrains names to _TOOLS
                result = {"error": f"Unknown tool {call.name!r}"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    # The server bounds content. Cutting serialized JSON here
                    # loses text and can remove revision/continuation metadata.
                    "content": json.dumps(result, separators=(",", ":")),
                }
            )

        if finish is not None:
            return finish


def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a retrieval call, returning errors to the model instead of raising."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- fed back to the model, not raised
        result: dict[str, Any] = {
            "error": str(exc),
            "remedy": getattr(exc, "remedy", ""),
        }
        server = getattr(exc, "server", None)
        if server is not None:
            result["server"] = server
        return result


def _finish(call: Any) -> Answer:
    """Return the model's answer and the citations it gave, as given."""
    raw = call.arguments.get("citations") or []
    return Answer(
        answer=str(call.arguments.get("answer", "")),
        citations=[str(c) for c in raw if isinstance(c, (str, int, float))],
    )
