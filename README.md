# team-pulse

Read a team's mined corpus — decisions, code and repo wikis, roster, reflection
questions — as structured data, or ask `ask-local` to retrieve from it and cite
its sources.

A library first, with a thin `team-pulse` CLI over it. Every deterministic
capability runs without a model provider; only `ask-local` needs one.

- [INSTALL.md](INSTALL.md)
- [CONFIGURATION.md](CONFIGURATION.md)

```bash
team-pulse search --q "renamed service"
team-pulse get --id members/jdoe
team-pulse ask-local --question "What did we decide about the rename?"
```

## Reading the corpus

Each one is a single call, and none needs a model provider.

| Verb | What it's for | Library | In `ask-local` |
|---|---|---|---|
| `info` | What this server has: its resource types and endpoints. First contact with an unfamiliar server, and the authoritative list for `resources --type`. | `info()` | yes |
| `resources` | List one kind of thing — "what reflection questions exist?", "who are the members?" When you know the kind but not the ids. Returns ids and titles only. | `resources()` | yes |
| `search` | Find it by words, when you remember a phrase but not where it lives. Substring matching, 50 results by default, 200 max. Returns ids and titles only. | `search()` | yes |
| `prefix` | List a branch: `members` returns every member, `questions` every question. Cheaper and more precise than `search` when you know the id's shape. | `prefix()` | yes |
| `get` | Read one page in full, by exact id (`members/jdoe`). The **only** verb that returns a body — the others return ids and titles. Answering without it means answering from titles. | `get()` | yes |

**The pattern:** `search` or `prefix` to find ids, then `get` on the ones worth
reading.

## Answering questions

| Verb | What it's for | Library | Needs a provider key |
|---|---|---|---|
| `ask-local` | Ask a question, get an answer with the sources it came from. A model drives the retrieval itself — deciding what to search, what to fetch, and when it has enough. | `ask_local()` | yes |
| `ask-service` | Let the server's own LLM answer. Best for interpreted questions — "how is the team tracking?" — rather than raw data. Fails loudly on HTTP 500; don't retry. | `ask_service()` | no |

Named for whose model runs: `ask-local` uses **yours** and returns what it
fetched; `ask-service` uses the **server's** and needs no key of your own.

## Writing

| Verb | What it's for | Library | In `ask-local` |
|---|---|---|---|
| `submit-answer` | Record a person's answer to a reflection question. Needs their GitHub username and the question's **bare slug** (`hard-questions`, not `questions/hard-questions`). Writes to shared team data. | `submit_answer()` | yes |

## Setup and introspection

| Verb | What it's for | Library |
|---|---|---|
| `status` | Which settings are in effect, and whether the server answers. Settings can arrive from three places at once, and this is how you see which won. Never raises. | `status()` |
| `configure` | Save your server's address so you stop passing it every time. | `configure()` |
| `manifest` | This tool's own machine-readable description, so a program can discover what it does without reading these docs. | `manifest()` |

## Behavior worth knowing

- **One JSON document per call on stdout.** Failures use the same shape —
  `{"error": {"type", "message", "remedy"}}` — plus one line on stderr, exit 1.
- **Preconditions are checked before anything is spent.** `ask-local` fails
  immediately if no provider key or no server is configured, rather than
  burning a model call to discover it.
- **The one write has no gate, deliberately.** `submit-answer` reaches
  shared team data the moment you call it. A confirmation flag cannot tell
  who set it, so it enforced nothing while implying it did -- and the
  bundle had none. The capability description carries the warning instead.
- **Importing costs nothing.** `import team_pulse` succeeds in an empty
  environment; credentials resolve only when a capability is called.

## As a library

```python
import team_pulse as tp
from team_pulse import Config

cfg = Config(url="https://your-team-pulse-host", key="tp_...")

hits = tp.search("renamed service", config=cfg)
result = tp.ask_local("What did we decide about the rename?", config=cfg)
print(result.answer)
```

**A library caller never has to touch environment variables.** Pass a `Config`
and nothing ambient is read. Omit it — `tp.search("...")` — and the settings
come from the environment and `~/.team-pulse/.env`, which is what the CLI does.

Everything the CLI does, the library does. The CLI only parses arguments and
shapes errors.
