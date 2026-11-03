# SPDX-License-Identifier: MIT
"""The one description of every capability, for every surface.

A capability is described in exactly one place -- here -- and the CLI, the
library's `capabilities()`/manifest, the Amplifier adapter and `ask_local`'s
model loop all derive from it. Nothing restates a name or a description.

That is the point of this module. The same capability used to carry three
different names and three different descriptions depending on which surface a
caller arrived through, so what a model was told about `search` depended on how
it found it. A caller that discovers a tool one way and invokes it another must
not get two different stories.

Descriptions are the bundle's, verbatim, wherever the bundle has a counterpart.
Each deviation is marked DEVIATION with the fact that forces it.

Names carry the `team_pulse_` prefix on model-facing surfaces because the bare
words -- search, prefix, answer, get -- are also ordinary English, and
appear as ordinary English inside these very descriptions. The CLI and the
library get the bare verb, where the command name (`team-pulse search`) and the
module (`team_pulse.search`) already supply that separation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Argument:
    """One argument, described once for every surface that accepts it."""

    name: str
    type: str
    help: str
    default: str | None = None
    required: bool = False
    enum: tuple[str, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None
    pattern: str | None = None
    #: Not offered to the model: the loop supplies it, or it is caller-only.
    model_hidden: bool = False

    def as_manifest_dict(self) -> dict[str, Any]:
        """The shape `capabilities()` and the manifest publish."""
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "help": self.help,
        }

    def as_json_schema(self) -> dict[str, Any]:
        """The shape a model provider's tool schema wants."""
        json_type = {
            "str": "string",
            "int": "integer",
            "bool": "boolean",
            "dict": "object",
            "list": "array",
        }.get(self.type.split("|")[0], "string")
        prop: dict[str, Any] = {"type": json_type, "description": self.help}
        if self.enum is not None:
            prop["enum"] = list(self.enum)
        if self.minimum is not None:
            prop["minimum"] = self.minimum
        if self.maximum is not None:
            prop["maximum"] = self.maximum
        if self.pattern is not None:
            prop["pattern"] = self.pattern
        if self.default is not None and json_type in ("integer", "number"):
            prop["default"] = int(self.default)
        return prop


@dataclass(frozen=True)
class Capability:
    """One capability: its name, its description, its arguments. Once."""

    #: CLI verb and library function name, e.g. `search`.
    verb: str
    #: The description. Every surface uses this text.
    description: str
    returns: str
    arguments: tuple[Argument, ...] = ()
    destructive: bool = False
    model_backed: bool = False
    #: Callable by the model inside `ask_local`'s loop.
    in_model_loop: bool = False
    #: Present only in the model loop -- no CLI verb, no library function.
    model_loop_only: bool = False

    @property
    def tool_name(self) -> str:
        """The name. One per capability, identical on every surface."""
        return self.verb

    @property
    def cli_verb(self) -> str:
        """The CLI spelling: hyphens, not underscores."""
        return self.verb.replace("_", "-")

    def as_json_schema(self) -> dict[str, Any]:
        props = {
            a.name: a.as_json_schema() for a in self.arguments if not a.model_hidden
        }
        required = [a.name for a in self.arguments if a.required and not a.model_hidden]
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    def as_tool(self) -> dict[str, Any]:
        """The provider tool definition for `ask_local`'s loop."""
        return {
            "name": self.tool_name,
            "description": self.description,
            "input_schema": self.as_json_schema(),
        }


# --------------------------------------------------------------------------
# The catalog. Bundle descriptions verbatim; deviations marked.
# --------------------------------------------------------------------------

CATALOG: tuple[Capability, ...] = (
    Capability(
        verb="info",
        description=(
            "Fetch the team-pulse lens API self-description: name, version, "
            "available resource_types, capabilities, and endpoint catalog. "
            "Use this first when you don't yet know what the API exposes."
        ),
        returns="dict (server-defined shape)",
        in_model_loop=True,
    ),
    Capability(
        verb="resources",
        description=(
            "List team-pulse resources. Returns the list envelope "
            "{resources: [{id, title, type}], count}. Filter by ``type`` for "
            # DEVIATION: the bundle enumerates
            # (team | outcomes | initiative | project | member | task) here.
            # Four of those six do not exist on the server, and it omits
            # `question`, which does -- so the enumeration is replaced by a
            # pointer to the authoritative list.
            "a specific class of resource; `info` reports the "
            "resource_types this server actually holds."
        ),
        returns="{count, resources: [{id, title, type}]}",
        arguments=(
            Argument(
                name="type",
                type="str|None",
                default="None",
                help="Optional resource type filter.",
            ),
        ),
        in_model_loop=True,
    ),
    Capability(
        verb="search",
        description=(
            "Naive text search across all team-pulse resources. Returns the "
            "same list envelope as `resources`. Default limit 50, "
            "max 200. Search is substring-matchy -- for precise lookups by ID "
            "prefer `get` or `prefix`."
        ),
        returns="{count, resources: [{id, title, type}]}",
        arguments=(
            Argument(name="q", type="str", help="Search query string.", required=True),
            Argument(
                name="limit",
                type="int",
                default="50",
                help="Max results (1-200, default 50).",
                minimum=1,
                maximum=200,
            ),
        ),
        in_model_loop=True,
    ),
    Capability(
        verb="prefix",
        description=(
            "List resources whose ID starts with the given path prefix. "
            # DEVIATION: the bundle's example is prefix='projects'. There is no
            # `project` resource type on the server; `members` is live.
            "Example: prefix='members' returns all members. Cheaper and more "
            "precise than `search` for hierarchical lookups."
        ),
        returns="{count, resources: [{id, title, type}]}",
        arguments=(
            Argument(
                name="prefix",
                type="str",
                # DEVIATION: the bundle's example names 'projects'.
                help="ID prefix, e.g. 'questions' or 'members'.",
                required=True,
            ),
        ),
        in_model_loop=True,
    ),
    Capability(
        verb="get",
        description=(
            "Fetch a single resource by full ID. Returns the resource "
            "envelope {id, title, type, data, metadata}. Examples of valid "
            # DEVIATION: the bundle's examples are 'projects/team-pulse',
            # 'members/samueljklee' and 'initiatives/onboarding'. Two name
            # resource types the server no longer has.
            "IDs: 'members/devis', 'questions/hard-questions'. Returns a 404 "
            "envelope if unknown."
        ),
        returns="dict (server-defined shape)",
        arguments=(
            Argument(
                name="id",
                type="str",
                # DEVIATION: example id, as above.
                help="Full resource ID (e.g. 'members/devis').",
                required=True,
            ),
        ),
        in_model_loop=True,
    ),
    Capability(
        verb="submit_answer",
        description=(
            "Submit a session-mined answer to a team-pulse reflection "
            "question. Use this to record an AI-generated answer attributed "
            "to a specific user, synthesized from their Context Intelligence "
            "sessions.\n\n"
            # There is no confirmation flag. One cannot tell who set it, so
            # it enforced nothing while implying it did -- and the bundle had
            # none. This paragraph is the whole guard.
            "WRITES TO SHARED TEAM DATA, attributed to a named person and "
            "visible to their team. Nothing in this tool can stop the call: "
            "if you are an agent, put the `user_id`, `question_id` and answer "
            "text to the user and get their approval BEFORE calling it.\n\n"
            "question_id is the BARE SLUG (e.g. 'higher-level-work'), NOT the "
            "hierarchical 'questions/<slug>' form -- strip the 'questions/' "
            "prefix if you have it. Discover valid slugs via "
            "`resources`(type='question') and use the "
            # DEVIATION: the bundle names `data.id`; the server returns
            # `data.question_id`.
            "data.question_id field (or strip the prefix from the "
            "list-envelope id).\n\n"
            "user_id is the github username of the person the answer is "
            "about; the API stores it verbatim and resolves to a team member "
            "at read time. generated_at is the ISO-8601 timestamp when the "
            "answer was generated."
            # DEVIATION: the bundle also documents source_session_ids as a
            # required field. The server's own AnswerSubmitBody no longer has
            # it -- its required set is {question_id, user_id, answer,
            # generated_at} -- so this client matches the current schema.
        ),
        returns="{id, question_id, created}",
        arguments=(
            Argument(
                name="user_id",
                type="str",
                help="GitHub username of the person the answer is about.",
                required=True,
            ),
            Argument(
                name="question_id",
                type="str",
                help=(
                    "Bare question slug (e.g. 'higher-level-work'). "
                    "Hierarchical 'questions/<slug>' is rejected."
                ),
                required=True,
                pattern="^[a-z0-9][a-z0-9-]*$",
            ),
            Argument(name="answer", type="str", help="The answer body.", required=True),
            Argument(
                name="generated_at",
                type="str|None",
                default="None",
                help="ISO-8601 timestamp when the answer was generated.",
            ),
            Argument(
                name="metadata",
                type="dict|None",
                default="None",
                help="Opaque provenance bag.",
                model_hidden=True,
            ),
        ),
        destructive=True,
        in_model_loop=True,
    ),
    Capability(
        verb="ask_service",
        description=(
            "Ask Team Pulse a natural-language question and get a live, "
            "generated answer as markdown content (AskResponse). The response "
            "`content` field contains a markdown-formatted answer the agent "
            "can read and reason over directly -- NOT an HTML fragment. Also "
            "includes `prompt_used` and `provenance` (sources + generated_at "
            "timestamp). This is the ONLINE GENERATION doorway -- it composes "
            "an answer over current team data, unlike the read-only "
            "read-only tools which return raw facts. Optional `focus` is a "
            # DEVIATION: the bundle's example is 'projects/team-pulse', a
            # resource type the server no longer has.
            "lens resource id (e.g. 'members/devis') used as a one-line "
            "orientation hint, not a filter."
        ),
        returns="{content, prompt_used, provenance}",
        arguments=(
            Argument(
                name="prompt",
                type="str",
                help="The question to ask Team Pulse.",
                required=True,
            ),
            Argument(
                name="focus",
                type="str|None",
                default="None",
                help=(
                    "Optional lens resource id for orientation (e.g. 'members/devis')."
                ),
            ),
        ),
        # The bundle mounted this tool but left it off its mode's `safe` list --
        # the only one of its eight excluded there. Not in the loop, as there.
        in_model_loop=False,
    ),
    # ----------------------------------------------------------------------
    # No bundle counterpart below this line.
    # ----------------------------------------------------------------------
    Capability(
        verb="ask_local",
        description=(
            "Answer a question by retrieving from the corpus with a "
            "model-backed loop, returning the answer and the resource ids it "
            "rests on. Uses YOUR model provider, unlike `ask_service` "
            "which spends the server's."
        ),
        returns="Answer{answer, citations}",
        arguments=(
            Argument(
                name="question",
                type="str",
                help="The question to answer.",
                required=True,
            ),
        ),
        model_backed=True,
    ),
    Capability(
        verb="status",
        description=(
            "Report which settings are in effect and whether the server "
            "answers. No secrets are included, and this never raises: "
            "describing a broken setup is its job."
        ),
        returns="{tool, version, config: {...}, reachability: {...}}",
    ),
    Capability(
        verb="configure",
        description=(
            "Persist the team-pulse endpoint URL (and optional Azure AD app "
            "id) to this machine's settings file, so it need not be supplied "
            "again."
        ),
        returns="{saved_url, persisted_path}",
        arguments=(
            Argument(
                name="url",
                type="str",
                help="https:// endpoint URL.",
                required=True,
            ),
            Argument(
                name="client_id",
                type="str|None",
                default="None",
                help="Azure AD app id override.",
            ),
        ),
        # NOT destructive: this writes the caller's own settings file on their
        # own machine, merging rather than replacing. `destructive` is reserved
        # for writes that reach shared data other people can see.
        destructive=False,
    ),
    Capability(
        verb="manifest",
        description=(
            "Return this tool's manifest as structured data: what it is, what "
            "it requires, and every capability with its arguments."
        ),
        returns=(
            "Manifest{smart_tool_format, name, version, description, "
            "use_cases, platforms, requires}"
        ),
    ),
    # The loop's terminator. No bundle counterpart: the bundle's agent ended
    # its turn by stopping, and the runtime noticed. A standalone loop has
    # nothing watching, so the model needs a way to say it is finished.
    Capability(
        verb="answer",
        description=(
            "Give the final answer and finish. Call this once the question "
            "can be answered; call nothing afterwards."
        ),
        returns="Answer{answer, citations}",
        arguments=(
            Argument(name="answer", type="str", help="The answer text.", required=True),
            Argument(
                name="citations",
                type="list",
                help=(
                    "Full resource ids supporting the answer, e.g. "
                    "'members/devis'. Empty if none."
                ),
                required=True,
            ),
        ),
        in_model_loop=True,
        model_loop_only=True,
    ),
)

#: Lookup by CLI verb / library function name.
BY_VERB: dict[str, Capability] = {c.verb: c for c in CATALOG}
#: Lookup by model-facing tool name.
BY_TOOL_NAME: dict[str, Capability] = {c.tool_name: c for c in CATALOG}


def model_loop_tools() -> list[dict[str, Any]]:
    """Tool definitions for `ask_local`'s model loop, in catalog order."""
    return [c.as_tool() for c in CATALOG if c.in_model_loop]


def public_capabilities() -> list[Capability]:
    """Capabilities with a CLI verb and a library function."""
    return [c for c in CATALOG if not c.model_loop_only]
