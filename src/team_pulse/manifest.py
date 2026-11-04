"""Manifest accessor. Callers reach the manifest through the library.

SMART_TOOL.md is the SOURCE OF TRUTH at the distribution root, where anything
reading the source -- a person browsing, CI, a registry scraping the repo --
finds it without being told where to look.

A Python wheel carries only package data, so a root-level file would not reach
an installed library. The build therefore copies it to ``team_pulse/_packaged/``
(see the force-include stanza in pyproject.toml), and this module reads that
copy first, falling back to the source tree for editable installs. Both paths
resolve to the same authored bytes; only the root file is ever edited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

PACKAGE = "team_pulse"
_MANIFEST_NAME = "SMART_TOOL.md"


class ManifestUnavailable(RuntimeError):
    """The packaged SMART_TOOL.md could not be located or parsed."""


@dataclass(frozen=True)
class Requirement:
    name: str
    purpose: str
    install: str
    optional: bool = False


@dataclass(frozen=True)
class Manifest:
    smart_tool_format: int
    name: str
    version: str
    description: str
    use_cases: list[str]
    platforms: list[str]
    requires: list[Requirement] = field(default_factory=list)
    body: str = ""

    def to_dict(self) -> dict[str, Any]:
        from team_pulse.catalog import public_capabilities

        return {
            "smart_tool_format": self.smart_tool_format,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "use_cases": list(self.use_cases),
            "platforms": list(self.platforms),
            "requires": [
                {
                    "name": r.name,
                    "purpose": r.purpose,
                    "install": r.install,
                    "optional": r.optional,
                }
                for r in self.requires
            ],
            # The body is the spec's guidance section -- "when this tool is the
            # right choice and when it is not, sharp edges, worked invocations."
            # Dropping it here made `manifest` return frontmatter only, so a
            # caller reading the manifest to learn how to use the tool got
            # nothing that told them.
            "body": self.body,
            # Callable schemas are derived from the same catalog as model tools.
            # Host-only Config/path settings are intentionally not model arguments.
            "capabilities": [
                {
                    "name": cap.verb,
                    "description": cap.description,
                    "input_schema": cap.as_json_schema(include_hidden=True),
                    "returns": cap.returns,
                    "destructive": cap.destructive,
                    "model_backed": cap.model_backed,
                }
                for cap in public_capabilities()
            ],
        }


def _manifest_text() -> str:
    """Read the manifest from the built wheel, falling back to the source tree."""
    packaged = resources.files(PACKAGE) / "_packaged" / _MANIFEST_NAME
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    # Editable install: src/<pkg>/manifest.py -> distribution root is parents[2].
    source = Path(__file__).resolve().parents[2] / _MANIFEST_NAME
    if source.is_file():
        return source.read_text(encoding="utf-8")
    raise ManifestUnavailable(
        f"{_MANIFEST_NAME} was not found in the installed package or the source tree. "
        "Reinstall the distribution."
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        raise ManifestUnavailable(
            f"{_MANIFEST_NAME} does not begin with YAML frontmatter."
        )
    _, _, rest = text.partition("---\n")
    front, sep, body = rest.partition("\n---")
    if not sep:
        raise ManifestUnavailable(
            f"{_MANIFEST_NAME} frontmatter is not terminated by '---'."
        )
    return front, body.lstrip("\n")


def manifest() -> Manifest:
    """Return the tool's manifest as structured data."""
    front, body = _split_frontmatter(_manifest_text())
    data = yaml.safe_load(front) or {}
    return Manifest(
        smart_tool_format=int(data["smart_tool_format"]),
        name=str(data["name"]),
        version=str(data["version"]),
        description=str(data["description"]).strip(),
        use_cases=[str(u) for u in data.get("use_cases", [])],
        platforms=[str(p) for p in data.get("platforms", [])],
        requires=[
            Requirement(
                name=str(r["name"]),
                purpose=str(r["purpose"]).strip(),
                install=str(r.get("install", "")),
                optional=bool(r.get("optional", False)),
            )
            for r in data.get("requires", [])
        ],
        body=body,
    )
