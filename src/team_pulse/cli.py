"""Thin JSON CLI for the team-pulse smart tool. Contains no domain logic.

`-h` and `--help` are deliberately NOT aliases:
  -h      terse, scannable summary
  --help  complete listing: every verb, its arguments (types/defaults), and
          what it returns, marking model-backed capabilities

Every capability lives in the `team_pulse` library; this module only parses
arguments, calls the library, and formats the result.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, NoReturn

import team_pulse as tp

PROG = "team-pulse"


class _EnvelopeArgumentParser(argparse.ArgumentParser):
    """An ArgumentParser whose usage errors speak the tool's own error envelope."""

    def error(self, message: str) -> NoReturn:
        payload = {
            "error": {
                "type": "UsageError",
                "message": message,
                "remedy": f"Run `{self.prog} --help` for the accepted arguments.",
            }
        }
        print(json.dumps(payload, indent=2))
        print(f"{self.prog}: {message}", file=sys.stderr)
        raise SystemExit(1)


def _short_help() -> str:
    lines = [
        f"{PROG} - Team Pulse lens API as structured data, plus a model-backed answer",
        "",
    ]
    for cap in tp.capabilities():
        marks = []
        if cap.model_backed:
            marks.append("model-backed")
        if cap.destructive:
            marks.append("writes to shared data")
        mark = f" [{', '.join(marks)}]" if marks else ""
        verb = cap.name.replace("_", "-")
        first = cap.description.split(". ")[0].rstrip(".") + "."
        lines.append(f"  {verb:<16} {first}{mark}")
    lines += ["", f"Run `{PROG} --help` for the complete listing."]
    return "\n".join(lines)


def _long_help() -> str:
    lines = [f"{PROG} - complete capability listing", ""]
    for cap in tp.capabilities():
        lines.append(cap.name.replace("_", "-"))
        lines.append(f"  description  : {cap.description.splitlines()[0]}")
        lines.append(f"  model-backed : {'yes' if cap.model_backed else 'no'}")
        lines.append(
            f"  destructive  : {'yes -- writes to shared data' if cap.destructive else 'no'}"
        )
        if cap.arguments:
            lines.append("  arguments    :")
            for arg in cap.arguments:
                default = arg.get("default")
                default_str = "(required)" if default is None else f"default={default}"
                lines.append(
                    f"    --{arg['name'].replace('_', '-'):<14} {arg['type']:<10} "
                    f"{default_str:<20} {arg.get('help', '')}"
                )
        else:
            lines.append("  arguments    : (none)")
        lines.append(f"  returns      : {cap.returns}")
        lines.append("")
    lines.append("Output is one JSON document on stdout on success, exit 0.")
    lines.append('Failure is {"error": {"type","message","remedy"}} on stdout, exit 1.')
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = _EnvelopeArgumentParser(prog=PROG, add_help=False)
    parser.add_argument("-h", action="store_true", dest="short_help")
    parser.add_argument("--help", action="store_true", dest="long_help")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("info", add_help=True)

    p_search = sub.add_parser("search", add_help=True)
    p_search.add_argument("--q", required=True)
    p_search.add_argument("--limit", type=int, default=50)

    p_prefix = sub.add_parser("prefix", add_help=True)
    p_prefix.add_argument("--prefix", required=True)

    p_get = sub.add_parser("get", add_help=True)
    p_get.add_argument("--id", required=True)

    p_resources = sub.add_parser("resources", add_help=True)
    p_resources.add_argument("--type", default=None)

    sub.add_parser("status", add_help=True)

    p_ask = sub.add_parser("ask-service", add_help=True)
    p_ask.add_argument("--prompt", required=True)
    p_ask.add_argument("--focus", default=None)

    p_submit = sub.add_parser("submit-answer", add_help=True)
    p_submit.add_argument("--user-id", required=True)
    p_submit.add_argument("--question-id", required=True)
    answer_body = p_submit.add_mutually_exclusive_group(required=True)
    answer_body.add_argument("--answer")
    answer_body.add_argument("--answer-file")
    p_submit.add_argument("--generated-at", default=None)
    p_submit.add_argument("--metadata", default=None, help="JSON object string.")

    p_configure = sub.add_parser("configure", add_help=True)
    p_configure.add_argument("--url", required=True)
    p_configure.add_argument("--client-id", default=None)

    p_answer = sub.add_parser("ask-local", add_help=True)
    p_answer.add_argument("--question", required=True)

    sub.add_parser("manifest", add_help=True)

    return parser


def _read_file_arg(path: str | None) -> str | None:
    """Read a --*-file companion argument into a string. A CLI convenience
    only -- the library itself only ever accepts data, never a path."""
    if path is None:
        return None
    from pathlib import Path

    return Path(path).expanduser().read_text(encoding="utf-8")


def _run(args: argparse.Namespace) -> Any:
    if args.command == "info":
        return tp.info()
    if args.command == "search":
        return tp.search(args.q, limit=args.limit)
    if args.command == "prefix":
        return tp.prefix(args.prefix)
    if args.command == "get":
        return tp.get(args.id)
    if args.command == "resources":
        return tp.resources(
            type=args.type,
        )
    if args.command == "status":
        return tp.status()
    if args.command == "ask-service":
        return tp.ask_service(args.prompt, focus=args.focus)
    if args.command == "submit-answer":
        answer_text = (
            args.answer if args.answer is not None else _read_file_arg(args.answer_file)
        )
        metadata = json.loads(args.metadata) if args.metadata else None
        return tp.submit_answer(
            args.user_id,
            args.question_id,
            answer_text or "",
            generated_at=args.generated_at,
            metadata=metadata,
        )
    if args.command == "configure":
        return tp.configure(args.url, client_id=args.client_id)
    if args.command == "ask-local":
        return tp.ask_local(args.question).to_dict()
    if args.command == "manifest":
        return tp.manifest().to_dict()
    raise SystemExit(_short_help())


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.long_help:
        print(_long_help())
        return 0
    if args.short_help or args.command is None:
        print(_short_help())
        return 0
    try:
        print(json.dumps(_run(args), indent=2))
    except Exception as exc:  # noqa: BLE001 - the CLI is the error boundary
        envelope: dict[str, Any] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "remedy": getattr(exc, "remedy", "See the tool documentation."),
        }
        # The server's own {code, message, status}, verbatim, when there was
        # one. Top level is this tool's; `server` is the server's.
        server = getattr(exc, "server", None)
        if server:
            envelope["server"] = server
        print(json.dumps({"error": envelope}, indent=2))
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
