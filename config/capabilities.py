from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from config.settings import DEVICE_CAPABILITIES_PATH

CapabilitiesConfig = dict[str, Any]

@dataclass(frozen=True)
class ActionCapability:
    device_type: str
    action: str
    generic: bool
    undo_action: str | None
    requires_auth: bool
    risk_level: str


@lru_cache(maxsize=4)
def load_device_capabilities(
    path: Path = DEVICE_CAPABILITIES_PATH,
) -> CapabilitiesConfig:
    """Load and cache device capability policy."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_action_capability(
    device: str,
    action: str,
    capabilities: CapabilitiesConfig | None = None,
) -> ActionCapability:
    if capabilities is None:
        capabilities = load_device_capabilities()

    default_device_type = capabilities.get("default_device_type", "switch")

    device_type = capabilities.get("device_types_by_device", {}).get(
        device,
        default_device_type,
    )

    action_policy = (
        capabilities.get("actions_by_device_type", {})
        .get(device_type, {})
        .get(action)
    )

    if action_policy is None:
        raise ValueError(
            f"Unsupported command capability: {device}.{action} "
            f"for device_type={device_type}"
        )

    return ActionCapability(
        device_type=device_type,
        action=action,
        generic=bool(action_policy.get("generic", False)),
        undo_action=action_policy.get("undo_action"),
        requires_auth=bool(action_policy.get("requires_auth", False)),
        risk_level=str(action_policy.get("risk_level", "unknown")),
    )


def is_special_command(
    device: str,
    action: str,
    capabilities: CapabilitiesConfig | None = None,
) -> bool:
    if capabilities is None:
        capabilities = load_device_capabilities()

    return any(
        item.get("device") == device and item.get("action") == action
        for item in capabilities.get("special_commands", [])
    )


def action_requires_face_auth(
    device: str,
    action: str,
    capabilities: CapabilitiesConfig | None = None,
) -> bool:
    return resolve_action_capability(
        device=device,
        action=action,
        capabilities=capabilities,
    ).requires_auth