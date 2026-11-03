"""team-pulse: Team Pulse lens API as structured data, plus a model-backed answer.

The library is the tool: every capability is a function importable from here.
The CLI (`team_pulse.cli`) and the Amplifier module adapter
(`amplifier_module_tool_team_pulse`) marshal arguments and shape errors ONLY --
zero domain logic in either wrapper.

Credentials are read at CALL time, never at import time: `import team_pulse`
succeeds with a completely empty environment.

`team_pulse.client`, `.config`, `.auth`, `.models`, and `.errors` are the
standalone async client library for the Team Pulse lens API (see the module
docstrings in each for details). Everything else here -- `api`, `model`,
`ask_local`, `prompts`, `manifest`, `cli` -- is this distribution's own bounded
loop and adapters built on top of that client.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from team_pulse.api import (
    TeamPulseNotConfigured,
    ask_service,
    configure,
    get,
    info,
    prefix,
    resources,
    search,
    status,
    submit_answer,
)
from team_pulse.ask_local import Answer, ask_local
from team_pulse.catalog import public_capabilities
from team_pulse.config import Config
from team_pulse.errors import (
    TeamPulseAPIError,
    TeamPulseAuthError,
    TeamPulseConnectionError,
    TeamPulseError,
)
from team_pulse.manifest import Manifest, ManifestUnavailable, Requirement, manifest
from team_pulse.model import ModelCallFailed, NoProviderConfigured

__version__ = "0.1.0"


@dataclass(frozen=True)
class CapabilityInfo:
    """One capability, described for a caller deciding how to invoke it.

    Built from `team_pulse.catalog`, which is the single place any capability
    is named or described. A caller that discovers this tool through the
    manifest and one that drives it through a model loop read the same text.
    """

    name: str
    description: str
    model_backed: bool
    destructive: bool
    arguments: list[dict[str, Any]]
    returns: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capabilities() -> list[CapabilityInfo]:
    """Describe this tool's surface, derived from the catalog.

    Includes which paths are destructive (they write to shared data) and
    which are model-backed (require a provider key).
    """
    return [
        CapabilityInfo(
            name=cap.verb,
            description=cap.description,
            model_backed=cap.model_backed,
            destructive=cap.destructive,
            arguments=[a.as_manifest_dict() for a in cap.arguments],
            returns=cap.returns,
        )
        for cap in public_capabilities()
    ]


__all__ = [
    "Answer",
    "CapabilityInfo",
    "Config",
    "Manifest",
    "ManifestUnavailable",
    "ModelCallFailed",
    "NoProviderConfigured",
    "Requirement",
    "TeamPulseAPIError",
    "TeamPulseAuthError",
    "TeamPulseConnectionError",
    "TeamPulseError",
    "TeamPulseNotConfigured",
    "__version__",
    "ask_local",
    "ask_service",
    "capabilities",
    "configure",
    "get",
    "info",
    "manifest",
    "prefix",
    "resources",
    "search",
    "status",
    "submit_answer",
]
