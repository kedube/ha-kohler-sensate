"""Shared base entity for Kohler Sensate."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SensateCoordinator


class SensateEntity(CoordinatorEntity[SensateCoordinator]):
    """Base entity: attaches all entities to the one faucet device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.api.device_id or "sensate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer="Kohler",
            model="Sensate (Konnect)",
            name=coordinator.api.device_name or "Sensate",
            sw_version=str(coordinator.about.get("firmware", {}).get("version") or ""),
            serial_number=coordinator.about.get("serialNumber"),
        )

    @property
    def _device_id(self) -> str:
        return self.coordinator.api.device_id or "sensate"
