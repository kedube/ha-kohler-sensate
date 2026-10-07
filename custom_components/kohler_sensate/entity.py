"""Shared base entity for Kohler Sensate."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SensateCoordinator


def _text(value: Any, nested_key: str = "version") -> str | None:
    """Return ``value`` as text; dicts like {"version": "16.0"} are unwrapped."""
    if isinstance(value, dict):
        value = value.get(nested_key)
    if value is None or value == "":
        return None
    return str(value)


class SensateEntity(CoordinatorEntity[SensateCoordinator]):
    """Base entity: attaches all entities to the one faucet device."""

    _attr_has_entity_name = True
    # Live faucet data and commands are meaningless while Kohler reports the
    # faucet offline; cloud-side data (leak history) and local settings aren't.
    _requires_online = True

    def __init__(self, coordinator: SensateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        about = coordinator.about
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_id)},
            manufacturer="Kohler",
            model="Sensate",
            model_id=_text(about.get("model")) or "SEN",
            name=coordinator.config_entry.title,
            sw_version=_text(about.get("firmware")),
            hw_version=_text(about.get("hardware")),
            serial_number=_text(about.get("serialNumber") or about.get("serial")),
        )

    @property
    def available(self) -> bool:
        if self._requires_online and not self.coordinator.faucet_online:
            return False
        return super().available
