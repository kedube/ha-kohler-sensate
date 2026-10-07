"""Update platform: whether Kohler has newer firmware for the faucet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SensateConfigEntry
from .coordinator import SensateCoordinator
from .entity import SensateEntity, as_text

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SensateFirmwareUpdate(entry.runtime_data)])


class SensateFirmwareUpdate(SensateEntity, UpdateEntity):
    """Installed and latest firmware, as Kohler's cloud reports them.

    Kohler's install command isn't known, so updates are installed from the
    Konnect app; this entity only reports them. It has no name of its own,
    so Home Assistant names it after its device class, "Firmware", in the
    user's language.
    """

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # Reports installs started from the app; can't start one itself.
    _attr_supported_features = UpdateEntityFeature.PROGRESS
    # Firmware details come from Kohler's cloud, not the live faucet.
    _requires_online = False

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "firmware")

    @property
    def _firmware(self) -> dict[str, Any]:
        firmware = self.coordinator.about.get("firmware")
        return firmware if isinstance(firmware, dict) else {}

    @property
    def installed_version(self) -> str | None:
        return as_text(self.coordinator.about.get("firmware"))

    @property
    def latest_version(self) -> str | None:
        # No reported latest version: assume the installed one is current.
        return as_text(self._firmware.get("latestVersion")) or self.installed_version

    @property
    def in_progress(self) -> bool:
        configuration = self.coordinator.config.get("configuration")
        if isinstance(configuration, dict) and configuration.get("otaInProgress"):
            return True
        progress = self._firmware.get("progress")
        return isinstance(progress, str) and progress.lower().endswith("inprogress")
