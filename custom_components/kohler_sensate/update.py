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

    The latest version comes from Kohler's firmware check, as in the Konnect
    app. Installing isn't offered: the app's install command hasn't been
    tried against a faucet yet, so updates are installed from the app. It has
    no name of its own, so Home Assistant names it after its device class,
    "Firmware", in the user's language.
    """

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    # Reports installs started from the app; can't start one itself.
    _attr_supported_features = UpdateEntityFeature.PROGRESS
    # Firmware details come from Kohler's cloud, not the live faucet.
    _requires_online = False

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "firmware")

    @property
    def entity_picture(self) -> str | None:
        # Update entities show the integration's logo by default; use Home
        # Assistant's update icons, which also show when one is available.
        return None

    @property
    def _firmware(self) -> dict[str, Any]:
        firmware = self.coordinator.about.get("firmware")
        return firmware if isinstance(firmware, dict) else {}

    @property
    def installed_version(self) -> str | None:
        firmware = self.coordinator.firmware
        return as_text(self.coordinator.about.get("firmware")) or (
            firmware.current if firmware else None
        )

    @property
    def latest_version(self) -> str | None:
        installed = self.installed_version
        if (firmware := self.coordinator.firmware) is not None:
            # The app decides from firmwareUpdateAvailable alone.
            if firmware.available and firmware.latest:
                return firmware.latest
            return installed
        # No answer from the firmware check: fall back to the configuration,
        # and with nothing reported, assume the installed version is current.
        return as_text(self._firmware.get("latestVersion")) or installed

    @property
    def in_progress(self) -> bool:
        if self.coordinator.firmware_downloading:
            return True
        configuration = self.coordinator.config.get("configuration")
        if isinstance(configuration, dict) and configuration.get("otaInProgress"):
            return True
        progress = self._firmware.get("progress")
        return isinstance(progress, str) and progress.lower().endswith("inprogress")
