"""Volume units and dispense presets for metric and imperial (US) users.

Kohler's API always takes liters. Everything the user sees (quick-dispense
buttons, the dispense-amount number, the last-dispense sensor and the default
unit of the ``dispense`` action) follows the unit system chosen in the
integration options.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .const import (
    CONF_UNIT_SYSTEM,
    DISPENSE_MAX_ML,
    UNIT_SYSTEM_IMPERIAL,
    UNIT_SYSTEM_METRIC,
)

# Units accepted by the ``dispense`` action, in milliliters per unit. The US
# customary factors match the ones the Konnect app uses.
ML_PER_UNIT: dict[str, float] = {
    "ml": 1.0,
    "l": 1000.0,
    "fl_oz": 29.5735295625,
    "cup": 236.5882365,
    "qt": 946.352946,
    "gal": 3785.411784,
}
UNIT_LABELS: dict[str, str] = {
    "ml": "mL",
    "l": "L",
    "fl_oz": "fl oz",
    "cup": "cups",
    "qt": "qt",
    "gal": "gal",
}

# Home Assistant unit strings that may appear in restored state.
HA_UNIT_KEYS: dict[str, str] = {
    UnitOfVolume.MILLILITERS: "ml",
    UnitOfVolume.LITERS: "l",
    UnitOfVolume.FLUID_OUNCES: "fl_oz",
    UnitOfVolume.GALLONS: "gal",
}


def to_ml(amount: float, unit: str) -> float:
    """Convert an amount in ``unit`` to milliliters."""
    return amount * ML_PER_UNIT[unit]


def from_ml(ml: float, unit: str) -> float:
    """Convert milliliters to ``unit``."""
    return ml / ML_PER_UNIT[unit]


@dataclass(frozen=True, kw_only=True)
class QuickAmount:
    """A one-tap dispense button."""

    key: str  # unique-id suffix
    ml: float
    translation_key: str
    placeholders: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class UnitProfile:
    """How dispense amounts are presented for one unit system."""

    unit_system: str
    service_unit: str  # default unit of the ``dispense`` action's ``amount``
    number_unit: str  # key into ML_PER_UNIT
    number_native_unit: str  # Home Assistant unit string
    number_min: float
    number_max: float
    number_step: float
    number_default: float
    precision: int
    quick_amounts: tuple[QuickAmount, ...]


def _metric(ml: int) -> QuickAmount:
    label = f"{ml / 1000:g} L" if ml >= 1000 else f"{ml} mL"
    # Unique-id suffix kept identical to v0.1 so existing entities survive.
    return QuickAmount(
        key=f"{ml}ml",
        ml=float(ml),
        translation_key="quick_dispense",
        placeholders={"amount": label},
    )


def _imperial(key: str, unit: str, amount: float) -> QuickAmount:
    return QuickAmount(
        key=key, ml=to_ml(amount, unit), translation_key=f"dispense_{key}"
    )


METRIC = UnitProfile(
    unit_system=UNIT_SYSTEM_METRIC,
    service_unit="ml",
    number_unit="ml",
    number_native_unit=UnitOfVolume.MILLILITERS,
    number_min=10,
    number_max=DISPENSE_MAX_ML,
    number_step=10,
    number_default=250,
    precision=0,
    quick_amounts=tuple(_metric(ml) for ml in (50, 250, 500, 750, 1000, 2000, 3000)),
)

IMPERIAL = UnitProfile(
    unit_system=UNIT_SYSTEM_IMPERIAL,
    service_unit="fl_oz",
    number_unit="fl_oz",
    number_native_unit=UnitOfVolume.FLUID_OUNCES,
    number_min=0.5,
    number_max=384,  # 3 gallons, the Konnect app's largest amount
    number_step=0.5,
    number_default=8,
    precision=1,
    quick_amounts=(
        _imperial("quarter_cup", "cup", 0.25),
        _imperial("half_cup", "cup", 0.5),
        _imperial("one_cup", "cup", 1),
        _imperial("two_cups", "cup", 2),
        _imperial("one_quart", "qt", 1),
        _imperial("half_gallon", "gal", 0.5),
        _imperial("one_gallon", "gal", 1),
    ),
)

PROFILES: dict[str, UnitProfile] = {
    UNIT_SYSTEM_METRIC: METRIC,
    UNIT_SYSTEM_IMPERIAL: IMPERIAL,
}

# Every quick-dispense unique-id suffix, across both unit systems.
ALL_QUICK_KEYS: frozenset[str] = frozenset(
    q.key for profile in PROFILES.values() for q in profile.quick_amounts
)


def default_unit_system(hass: HomeAssistant) -> str:
    """Pick the unit system that matches Home Assistant's own setting."""
    if hass.config.units is US_CUSTOMARY_SYSTEM:
        return UNIT_SYSTEM_IMPERIAL
    return UNIT_SYSTEM_METRIC


def get_profile(hass: HomeAssistant, entry: ConfigEntry) -> UnitProfile:
    """Return the unit profile configured for ``entry``."""
    system = entry.options.get(CONF_UNIT_SYSTEM) or default_unit_system(hass)
    return PROFILES.get(system, METRIC)
