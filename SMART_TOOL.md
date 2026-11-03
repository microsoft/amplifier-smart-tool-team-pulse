---
smart_tool_format: 1
name: team-pulse
version: 0.1.0
description: >
  Reads the Team Pulse lens API -- a knowledge base mined for a team (decision/meeting
  wikis, code/repo wikis, roster, reflection questions) -- as structured data, plus a
  model-backed answer that retrieves and cites its sources. Use for factual questions
  about a team's decisions, what shipped, who owns what, why something happened, or how
  something works.
use_cases:
  - Answer "what did we decide about X" / "who owns Y" / "why did we do Z" with citations
  - Search or browse a team's mined corpus (decision wikis, code/repo wikis)
  - Look up the team roster or a reflection question by id
  - Bulk-download the corpus for offline analysis (embeddings, grep, your own agents)
  - Record a session-mined answer to a reflection question
platforms:
  - linux
  - macos
requires:
  - name: network access to the lens API
    purpose: >
      The configured Team Pulse endpoint URL must be reachable over HTTPS.
      Without it, no capability can run.
    install: CONFIGURATION.md
  - name: team-pulse credentials
    purpose: >
      Every capability needs either a per-user Azure AD bearer token (az login)
      or a shared API key. Without either, every capability raises with a remedy.
    install: CONFIGURATION.md
  - name: model provider credentials
    purpose: >
      Only `ask-local` needs OPENAI_API_KEY or ANTHROPIC_API_KEY. Every deterministic
      capability runs with no provider configured at all.
    optional: true
    install: CONFIGURATION.md
---

# team-pulse

Team Pulse lens API as structured data, plus `ask-local` -- a model-backed capability
that retrieves from the corpus and cites its sources. One library, one thin
`team-pulse` CLI.

## Install

```bash
uv tool install 'team-pulse @ git+https://github.com/microsoft/amplifier-smart-tool-team-pulse'
team-pulse status
```

No virtual environment is required and none should be created: `uv tool
install` builds the tool in an isolated environment and puts `team-pulse` on
PATH. Do **not** use `uv pip install` for the CLI -- it is a library install
and fails with `error: No virtual environment found` when no venv is active.

`status` runs with nothing configured and reports `"configured": false` on a
fresh install; that is success, not failure. Set the endpoint and credentials
next -- see `CONFIGURATION.md`.

Full matrix (upgrading, uninstalling, developing on the tool): `INSTALL.md`.

## Sharp edges

- **`ask-local` costs tokens.** It runs a model-backed retrieval loop using YOUR
  configured provider (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`). The model decides
  when it has enough to answer; there is no step limit. It is not `ask-service` (see below).
- **`ask-service` spends the SERVER's model budget, not yours.** It calls the Team
  Pulse server's own LLM (`POST /api/lens/ask`) and is a *different*
  capability from `ask-local`. Do not combine the two in one workflow.
- **`get` returns whatever the server sends.** Index/overview/log pages can be
  hundreds of KB; `search`/`prefix` are the cheaper way to locate a specific
  page.
- **One capability writes, and nothing stops it.** `submit_answer` records
  an answer attributed to a named person, visible to their team, as soon as
  you call it. There is no confirmation flag: one cannot tell who set it,
  so it would enforce nothing while implying it did, and the bundle this
  was ported from had none. If you are an agent, put the `user_id`,
  `question_id` and answer text to the user and get their approval BEFORE
  calling it. That is the whole guard.
- **The retrieval strategy ships verbatim** as `prompts/retrieval-strategy.md`
  and is used as `ask-local`'s system prompt. Nothing in this tool paraphrases it.

## Worked invocations

```bash
team-pulse status
team-pulse info
team-pulse search --q "renamed service"
team-pulse prefix --prefix members
team-pulse get --id members/jdoe
team-pulse resources --type question
team-pulse resources --type member
team-pulse ask-service --prompt "How is the team tracking?"
team-pulse submit-answer --user-id jdoe --question-id higher-level-work --answer "..."
team-pulse configure --url https://team-pulse.example.com
team-pulse ask-local --question "What did we decide about the rename?"
team-pulse manifest
```

## Which call answers which question

Written against what this server actually holds -- `info` reports
`resource_types: ['member', 'question']`, and the mined corpus is reachable by
id even though it is not a listed type. Check `info` for the server you are
pointed at.

| Question | Call |
|---|---|
| "Who is on the team?" | `resources --type member` |
| "Who is <person> and what do they work on?" | `get --id members/<handle>` -- one call: `.data` carries `projects`, `roles`, `primary_pod`, `pair_partner` |
| "What questions is the team being asked?" | `resources --type question` |
| "What is question <slug> asking?" | `get --id questions/<slug>` -- `.data.question` is the text |
| "Find anything about <topic>" | `search --q <topic>`, then `get` the ids worth reading |
| "What is in <document>?" | `get --id corpus/<path>` -- returns markdown in `content` |
| "What did we decide about X, and why?" | `ask-local --question "..."` -- retrieves across the corpus and cites the ids it used |
| "Is <thing> going well?" | Not this tool. Surface the fields, then judge separately. |

Two things worth knowing before writing a loop:

* **A member record is self-contained.** `projects` and `primary_pod` are on
  the member itself, so there is no second lookup to find what someone works
  on.
* **`search` reaches the corpus, `resources` does not.** `resources --type`
  lists entities; the mined documents answer to `search`, `prefix corpus` and
  `get`.

## Using it well

1. **Reach for it first on factual org/team questions.** If the question is
   "what did we decide about X", "who owns Y", or "why did we do Z", this tool
   answers from the team's own record rather than a guess or a web search.

2. **For a multi-step lookup, use `ask-local`.** It drives the retrieval itself
   -- deciding what to search, what to fetch, and when it has enough -- and
   returns the resource ids its answer rests on. Driving `search`/`get` by hand
   is for when you already know the shape of what you want.

3. **Prefer targeted lookups.** Use `resources`, `search` or `prefix` to find
   resource IDs, then `get` the ones worth reading.

4. **Surface errors verbatim.** Quote the envelope's `type` and `message` back
   rather than paraphrasing. The common ones are 404 (wrong id -- `search` or
   `prefix` will find the right one) and 401 (misconfigured -- surface it, do
   not retry).

5. **Read-mostly is the contract.** `submit_answer` is the one write, and it
   records an answer to a reflection question on behalf of a named person.
   Nothing else persists anything; do not promise to update or edit team data.

## When NOT to use it

- **Subjective judgment** -- "is project X at risk?", "is this person
  overloaded?". Surface the facts with this tool, then reason about them
  separately. The tool reports what the record says; it does not assess.
- **Anything outside this team's vault.** It knows one team's mined corpus and
  nothing else. For general questions, use another source.
- **Anything needing a write other than an answer submission.** There is no
  capability to create, edit or delete team data.

## Data model reference

> **Ported from the bundle's `context/using-team-pulse.md`, with capability
> names adapted and retired endpoints/options removed.** It predates the current
> server: the taxonomy below lists eight resource types, of which the live
> deployment keeps `member`, `question` and `doc`. `info` reports the authoritative
> `resource_types` for the server you are talking to -- trust it over this table,
> and read the ids in the examples as illustrative shapes rather than resources
> that exist.

### What team-pulse is

A small read-only HTTP service exposing structured data about a team — what
people work on, how that work rolls up, and current status. The data comes
from a markdown-based "vault" that team members edit by hand; the lens API
returns resource data suitable for tooling and assistants.

#### Resource taxonomy

| Type | What it represents | ID convention |
|---|---|---|
| `team` | The overall team — mission, charter, people directory. Usually a single resource. | `team/<slug>` |
| `outcomes` | Top-level outcomes the team is driving toward. | `outcomes/<id>` |
| `initiative` | A pod / multi-quarter effort. Rolls up to outcomes. | `initiatives/<id>` |
| `project` | A workstream inside an initiative. The most common type. | `projects/<id>` |
| `member` | A person on the team. | `members/<handle>` |
| `task` | A unit of work, assigned to a member, often tied to a project. | `tasks/<id>` |
| `doc` | Markdown design docs for projects, initiatives, and other long-form team writing. Hierarchical SLOP-style IDs. | `docs/<hierarchical/path>.md` |
| `question` | Admin-authored reflection prompts the team is currently being asked to think about. Admin-authored kebab-case slug IDs (e.g. `hard-questions`, `effective-practices`); text is treated as immutable in v0 — typo fixes require a new slug. | `questions/<slug>` |

Vault size evolves as the team adds work. To count what's currently in the
vault, call `resources()` (all types) or
`resources(type="<type>")` and read the response's `count` field.

Example `doc` IDs:

* `docs/base-camp/README.md`
* `docs/the-rig/resolvers/resolvers.md`
* `docs/program/INDEX.md`

#### The single-resource envelope

```json
{
  "id":       "projects/team-pulse",
  "title":    "Team Pulse",
  "type":     "project",
  "data":     { /* the canonical resource body */ },
  "metadata": { /* overlay info, last_modified, etc. */ }
}
```

#### The list envelope

```json
{
  "resources": [
    {"id": "projects/team-pulse", "title": "Team Pulse", "type": "project"},
    /* ... */
  ],
  "count": 21
}
```

Lists return ID/title/type only — they're cheap to fetch and intended for
discovery. Get the full body with `get`.

#### Envelope variant for `doc`

Single-resource fetches for `doc`-type resources use a slightly different
shape: the body comes back as raw markdown in a top-level `content` field
instead of a structured `data` dict.

```json
{
  "id":       "docs/the-rig/resolvers/resolvers.md",
  "title":    "Resolvers design doc",
  "type":     "doc",
  "content":  "<raw markdown source>",
  "metadata": {
    "source_path": "data/core/docs/the-rig/resolvers/resolvers.md",
    "format":      "markdown",
    "word_count":  1200,
    "byte_count":  6500
  }
}
```

This is normal polymorphic JSON — switch on the top-level `type` field:

* `type != "doc"` → read `data` (structured dict for the canonical body)
* `type == "doc"` → read `content` (raw markdown string)

The `title` for a doc is the first H1 line in the markdown, falling back
to the filename stem if no H1 is present. `metadata` is informational
(source path, word/byte counts, format).

Lists (`/api/lens/resources?type=doc`) and prefix walks behave normally:
ID/title/type only, same shape as every other list response.

#### The error envelope

```json
{"error": {"code": "not_found", "message": "...", "status": 404}}
```

Every error path uses this shape. The tool layer surfaces it verbatim in
`ToolResult.error` — you can quote `error.message` back to the user.

### Endpoint reference (mapped to tools)

| Tool | Wraps | When to use |
|---|---|---|
| `info()` | `GET /api/lens/info` | First call when you don't know the surface — returns the catalog of resource types, capabilities, and endpoints. |
| `resources(type=…)` | `GET /api/lens/resources` | "Give me all the projects." Cheap list operation. |
| `search(q="auth")` | `GET /api/lens/resources/search` | Free-text search across titles + bodies. Substring-matchy; good for fuzzy lookup, bad for precise queries. |
| `prefix("members")` | `GET /api/lens/resources/prefix/{p}` | Hierarchical listing — same effect as `resources(type=member)` for top-level prefixes, but works for any prefix. |
| `get(id="projects/team-pulse")` | `GET /api/lens/resources/{id}` | Fetch one resource by full ID. |
| `ask_service(prompt="…")` | `POST /api/lens/ask` | **Online generation.** Ask a natural-language question and get a composed, rendered answer over current team data. Use when you want an interpreted, synthesized answer rather than raw facts. |

### Common query patterns

#### "What's project X?"

```python
get(id="projects/X")          # full body
```

#### "List all projects."

```python
resources(type="project")     # list; read result.count for total
```

#### "Who's on initiative Y?"

The initiative resource lists members in its `data`. Fetch it:

```python
get(id="initiatives/Y")
# inspect result.data for member references like {handle: "samueljklee"}
```

#### "What's Sam working on?"

```python
get(id="members/samueljklee")
# data typically links to current projects/tasks
```

#### Fuzzy lookup

```python
search(q="onboarding", limit=20)
```

Returns a list envelope. Use the IDs to follow up with `get`
for full bodies.

For `doc`-type resources, `search` matches inside the full
markdown content body, not just the title — so it's the right entry
point for "what's documented about X?" style questions.

#### Working with questions

Reflection questions are the admin's "what should the team be thinking
about right now" surface. They use the standard entity envelope (body in
`data`, not `content`), so all the generic tools work without special
casing:

```python
# List every active question — read result.count for the total
# Returned in (created_at, id) order so display order follows authoring order.
resources(type="question")

# Fetch one question by its full hierarchical ID
get(id="questions/hard-questions")
# result.data is the question dict: {id, text, created_at, created_by}
# result.title is the question text (questions have no separate title field)

# Browse the whole namespace
prefix("questions")
```

Question schema (v0):

| Field | Notes |
|---|---|
| `id` | Admin-authored kebab-case slug (e.g. `hard-questions`, `effective-practices`). Stable opaque identifier — survives text edits because text edits aren't allowed (see immutability note). NOT a content hash. |
| `text` | The prompt itself. **Immutable** in v0 — typo fixes require a new slug. |
| `created_at` | ISO-8601 UTC timestamp the admin first added the question. Also serves as the sort key for display order. |
| `created_by` | The admin's short handle. |

The v0 surface is intentionally minimal: there is no `state` field,
no explicit `display_order` field, no `schema_version` yet. All three
are pure-additive and will arrive when needed without breaking this
contract. Ordering today is `(created_at, id)` from the loader; when
a UI exists that needs to support reorder, `display_order: int | null`
can be added without disturbing existing consumers.

Forward-compat: when answers ship, each answer will reference a question
by its bare slug (`question_id: "hard-questions"`, not
`"questions/hard-questions"`) — matching the existing FK convention
(`task.assignee_ids: ["samuel"]` references members by bare handle, not
`"members/samuel"`). The immutability rule means old answers stay valid;
if the admin wants to reword a question, they create a new slug and
archive the old one — answers to the old slug remain meaningful.

#### Working with answers

Answers are the responses to reflection questions. The bundle exposes one
write tool for submitting session-mined answers. There is no read tool for
answers in v0 — answer read access is handled by the team-pulse app UI.

##### Submitting an answer (`submit_answer`)

Use this tool to record an AI-generated answer attributed to a specific team
member, synthesized from their Context Intelligence sessions.

**Parameters:**

| Parameter | Required | Notes |
|---|---|---|
| `question_id` | yes | **Bare slug** — e.g. `higher-level-work`, NOT `questions/higher-level-work`. Strip the `questions/` prefix if you have it from a resource lookup. |
| `user_id` | yes | GitHub username of the person the answer is about (e.g. `colombod`). The bundle records it as a github-namespaced identity (`respondent: {provider: "github", id: <user_id>}`), stored **verbatim** — the server does NOT resolve it to a canonical handle at write time; resolution to a team member happens at read time. |
| `answer` | yes | The answer body. Min length 1. |
| `source_session_ids` | yes | Array of Context Intelligence session IDs the answer was synthesized from. May be empty `[]` if the mining run genuinely can't attribute to specific sessions, but should be populated when provenance is available. |
| `generated_at` | yes | ISO-8601 timestamp when the answer was generated (e.g. `2026-05-31T02:15:30.000+00:00`). |

**`source` is not a caller parameter.** The bundle hardcodes `"session-mining"`
when building the request — it is intrinsic to this tool's purpose. You never
pass `source`; you cannot accidentally or maliciously claim a different writer.

**`question_id` must be a bare slug.** The pattern `^[a-z0-9][a-z0-9-]*$` is
enforced at the schema level. If you have a hierarchical ID from a resource
lookup (`questions/higher-level-work`), strip the `questions/` prefix before
passing it here. This matches the FK convention used throughout the vault
(e.g. `task.assignee_ids` references members by bare handle, not
`members/<handle>`).

**Error codes** you may see from the server:

| Code | When |
|---|---|
| `unknown_question` | `question_id` doesn't match a known question |
| `unknown_respondent` | `user_id` resolves to no member of the team |
| `invalid_argument` | missing/empty required field, unparseable `generated_at` |

##### Worked example: discover question → submit answer

```python
# 1. List all active questions to find the right slug
questions = resources(type="question")
# result.resources: [{id: "questions/higher-level-work", title: "...", ...}, ...]

# 2. The list envelope uses hierarchical IDs — strip the prefix
question_id = "higher-level-work"  # NOT "questions/higher-level-work"

# 3. Submit the answer
result = submit_answer(
    question_id=question_id,
    user_id="colombod",               # github username of the analyzed person
    answer="Based on recent session analysis, ...",
    source_session_ids=[
        "846491a9-8082-4e0c-95f9-32b90a3d15a0",
        "fe291191-419d-4a97-b42b-d76e6193c5e7",
    ],
    generated_at="2026-05-31T02:15:30.000+00:00",
)
# On success: result.answer.{id, question_id, respondent_handle, answer, source, ...}
# On failure: result.error.{code, message, status}
```

The server returns the persisted record on `201`. The `respondent_handle` in
the response is the **canonical** short handle resolved from the `user_id` you
provided — use it to confirm the right person was attributed.

#### ask_service — online-generation doorway

`ask_service` is the **online-generation** tool. Unlike the read-only
`*` data tools (which return raw vault facts), `ask_service`
composes a synthesized, rendered answer over current team data by calling
`POST /api/lens/ask`.

Use it when you want an interpreted answer — "how is the team doing?",
"what's the status of initiative X?" — rather than raw structured data.

**Parameters:**

| Parameter | Required | Notes |
|---|---|---|
| `prompt` | yes | The question to ask. Min length 1. |
| `focus` | no | Optional lens resource id (e.g. `projects/team-pulse`). Used as an **orientation hint** — the server prepends one line `## Currently viewing\n{focus}` to the user message. Not a filter; does not isolate the answer to that resource. |

**`viewer` is never a caller parameter.** The server derives the viewer
identity from the api-key principal. You never pass `viewer`.

**Response shape (`AskResponse`):**

```json
{
  "content":     "## Team\n\n- **alice** is on Nexus…",
  "prompt_used": "the prompt sent to the generator",
  "provenance":  {
    "sources":      [{"type": "tasks", "count": 42, "date_range": "…"}],
    "generated_at": "2026-06-15T10:00:00"
  }
}
```

The `content` field is **markdown text** the agent can read and reason over
directly.  There is no `html`, no `pills`, and no `fallback` field.

**Fail-loud behavior.** There is no silent fallback. If the LLM generation
engine is unavailable, the API returns **HTTP 500** — surfaced to the tool
caller as a `TeamPulseAPIError` with `status: 500`. Do not retry
automatically; surface the error to the user.

**Worked example:**

```python
# Ask a general question
result = ask_service(prompt="How is the team tracking against its outcomes?")
# result.output["content"] contains the markdown answer

# Ask with focus orientation
result = ask_service(
    prompt="What's the status?",
    focus="projects/team-pulse",
)
# The server prepends "## Currently viewing\nprojects/team-pulse" to the prompt
# before generation — an orientation hint, not a filter.
```

#### Working with docs

The same generic tools work over docs as over entities — only the
single-resource envelope shape differs (see the envelope variant above).

```python
# List every doc — read result.count for the total
resources(type="doc")

# Fetch one doc by its full hierarchical ID
get(id="docs/the-rig/resolvers/resolvers.md")
# result.content is the raw markdown; result.data is absent for docs

# Browse a hierarchy
prefix("docs/base-camp")     # everything under base-camp
prefix("docs/the-rig")       # everything under the-rig

# Full-text search across both entity titles AND doc content bodies
search(q="resolvers")
# returns a mix of entity hits and doc hits — switch on result.type
```

The typical exploratory pattern is `search` → `get`: search to find the
doc IDs whose content matches a topic, then fetch the ones worth reading
in full.

##### Guards

Two server-side guards apply to `doc` IDs:

* Any path component containing `..` is rejected with **404** (path-traversal
  defense). Use forward-slash hierarchical IDs only.
* Any non-`.md` path under the `docs/` namespace returns **404**. Only
  markdown files are exposed.

### Error handling guidance

* **404** → the resource ID is wrong or doesn't exist. Try
  `prefix(...)` to discover valid IDs in that namespace, or
  `search(q=…)` for fuzzy lookup.
* **401 / missing_or_malformed_key** → the bundle's `key` config is
  unset, mistyped, or revoked. Surface the error code to the user; do
  NOT retry blindly.
* **transport_error** → network / DNS / timeout. Show the message; the
  caller decides whether to retry.

### Scope reminder (v1)

These tools answer **factual** questions about what's in the vault. They
do NOT reason about whether projects are at risk, whether an initiative
is well-scoped, or what someone *should* be working on. That's a
"thinking partner" role deferred to a future agent. If the user asks for
analysis, surface the facts and let the parent session reason about them.
