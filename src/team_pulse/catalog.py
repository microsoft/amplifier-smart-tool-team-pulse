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

Names are the library verbs on model-facing surfaces too. Descriptions and
arguments reflect this client's callable surface, not every server capability.
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
            "required": self.required,
            "schema": self.as_json_schema(),
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
        if self.default not in (None, "None") and json_type in ("integer", "number"):
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

    def as_json_schema(self, *, include_hidden: bool = False) -> dict[str, Any]:
        """Callable arguments; model tools omit caller-only provenance."""
        props = {
            a.name: a.as_json_schema()
            for a in self.arguments
            if include_hidden or not a.model_hidden
        }
        required = [
            a.name
            for a in self.arguments
            if a.required and (include_hidden or not a.model_hidden)
        ]
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
# The catalog.
# --------------------------------------------------------------------------

_COLLECTION = Argument(
    name="collection",
    type="str|None",
    default="None",
    help="Optional collection name from info; do not invent collection names.",
)
_RECORD_LIMIT = Argument(
    name="limit",
    type="int|None",
    default="None",
    minimum=1,
    help="Optional record count limit; omitted preserves the unbounded list default.",
)
_RECORD_OFFSET = Argument(
    name="offset",
    type="int",
    default="0",
    minimum=0,
    help="Record offset (not a UTF-8 document byte offset).",
)

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
        verb="whoami",
        description=(
            "Read the authenticated caller's server-verified identity via /api/lens/me. "
            "Use for 'me'/'my' when the server supports it; do not infer identity from "
            "a roster entry or local configuration. Errors remain errors."
        ),
        returns="dict (server-defined caller identity)",
        in_model_loop=True,
    ),
    Capability(
        verb="resources",
        description=(
            "List resources with optional type, collection and question-status filters. "
            "Discover valid types/collections via info. Optional limit/offset count "
            "records, not bytes; omitted limit keeps the unbounded default. Preserve "
            "the whole envelope: count is returned records, not necessarily a total."
        ),
        returns="list envelope (resources, count, and server metadata)",
        arguments=(
            Argument(
                name="type",
                type="str|None",
                default="None",
                help="Optional resource type filter.",
            ),
            _COLLECTION,
            Argument(
                name="status",
                type="str|None",
                default="None",
                enum=("active", "archived", "all"),
                help="Question status filter; omitted uses the server's active default.",
            ),
            _RECORD_LIMIT,
            _RECORD_OFFSET,
        ),
        in_model_loop=True,
    ),
    Capability(
        verb="search",
        description=(
            "Scoped text search for candidate resource IDs. Default limit 50, max 200; "
            "optional collection from info, no search offset. Snippets are discovery "
            "evidence, not complete documents: get selected exact IDs before answering "
            "from their bodies. Preserve per-hit content_revision and envelope metadata. "
            "count is returned hits, not total matches. Zero hits is success; HTTP 503 "
            "is a search failure, never 'nothing found'."
        ),
        returns="list envelope with hit content_revision when supplied by server",
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
            _COLLECTION,
        ),
        in_model_loop=True,
    ),
    Capability(
        verb="prefix",
        description=(
            "List resources whose ID starts with the given path prefix. "
            # DEVIATION: the bundle's example is prefix='projects'. There is no
            # `project` resource type on the server; `members` is live.
            "Example: prefix='members' browses members. Optional limit/offset count "
            "records, not bytes; omitted limit preserves the unbounded default. "
            "Read envelope metadata before claiming a complete inventory."
        ),
        returns="list envelope (resources, count, and server metadata)",
        arguments=(
            Argument(
                name="prefix",
                type="str",
                # DEVIATION: the bundle's example names 'projects'.
                help="ID prefix, e.g. 'questions' or 'members'.",
                required=True,
            ),
            _RECORD_LIMIT,
            _RECORD_OFFSET,
        ),
        in_model_loop=True,
    ),
    Capability(
        verb="get",
        description=(
            "Get one exact resource ID or one document page, never automatic full "
            "assembly. Entities return structured data unchanged. Documents return "
            "content, content_revision and metadata (offset, returned_bytes, total_bytes, "
            "next_offset, truncated). offset/limit measure UTF-8 content bytes, NOT JSON "
            "envelope bytes; default 32768, max 153600. Only if more evidence is needed, "
            "request metadata.next_offset with expected_revision=content_revision. "
            "A nonzero document offset requires a revision; HTTP 409 source_changed "
            "must stay an error, never automatically retry unpinned. Invalid arguments "
            "are HTTP 422; unknown IDs are HTTP 404."
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
            Argument(
                name="offset",
                type="int",
                default="0",
                minimum=0,
                help="UTF-8 document content byte offset; nonzero requires expected_revision.",
            ),
            Argument(
                name="limit",
                type="int|None",
                default="None",
                minimum=1,
                maximum=153600,
                help="Content byte budget; omitted uses server default 32768, not a JSON cap.",
            ),
            Argument(
                name="expected_revision",
                type="str|None",
                default="None",
                help="Full captured served-text content_revision, including lens-text-v1: prefix.",
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
            "text to the user and get their approval BEFORE calling it, subject "
            "to the host's policy. This guidance grants no authority.\n\n"
            "question_id is the BARE SLUG (e.g. 'higher-level-work'), NOT the "
            "hierarchical 'questions/<slug>' form -- strip the 'questions/' "
            "prefix if you have it. Discover valid slugs via "
            "`resources`(type='question') and use the "
            # DEVIATION: the bundle names `data.id`; the server returns
            # `data.question_id`.
            "data.question_id field (or strip the prefix from the "
            "list-envelope id).\n\n"
            "user_id is the github username of the person the answer is "
            "about; inspect the server's persisted attribution in its reply. "
            "generated_at is the ISO-8601 timestamp when the "
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
            "tools which return raw facts. Use only when the user explicitly requests "
            "server-generated synthesis; prefer deterministic retrieval otherwise. "
            "Optional `focus` is a "
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
