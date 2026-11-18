"""Binary sensor platform for DomoLink Free Mobile."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FreeMobileDataUpdateCoordinator, FreeMobileLineData


def _has_unpaid(line: FreeMobileLineData) -> bool:
    return any(
        bool(line.unpaids.get(bucket))
        for bucket in ("personalUnpaids", "parentUnpaids", "childrenUnpaids")
    )


def _has_active_sim(line: FreeMobileLineData) -> bool:
    return any(str(sim.get("state", "")).lower() == "active" for sim in line.sims)


def _has_active_booster(line: FreeMobileLineData) -> bool:
    booster = line.offer.get("booster")
    if not isinstance(booster, dict) or not booster:
        return False
    state = str(booster.get("booster_state") or booster.get("state") or "").lower()
    if state:
        return state in ("active", "enabled", "on", "activated")
    return bool(booster.get("id") or booster.get("name"))


@dataclass(frozen=True, kw_only=True)
class FreeMobileBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Free Mobile binary sensor."""

    is_on_fn: Callable[[FreeMobileLineData], bool]
    extra_attrs_fn: Callable[[FreeMobileLineData], dict[str, Any]] | None = None


BINARY_SENSOR_DESCRIPTIONS: tuple[FreeMobileBinarySensorDescription, ...] = (
    FreeMobileBinarySensorDescription(
        key="five_g",
        name="5G activée",
        icon="mdi:signal-5g",
        is_on_fn=lambda l: bool(l.offer.get("fiveG", False)),
    ),
    FreeMobileBinarySensorDescription(
        key="freebox_advantage",
        name="Avantage Freebox",
        icon="mdi:router-wireless",
        is_on_fn=lambda l: l.has_freebox_advantage,
    ),
    FreeMobileBinarySensorDescription(
        key="unpaid_alert",
        name="Impayé détecté",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_has_unpaid,
    ),
    FreeMobileBinarySensorDescription(
        key="sim_active",
        name="SIM active",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=_has_active_sim,
    ),
    FreeMobileBinarySensorDescription(
        key="booster_active",
        name="Booster actif",
        icon="mdi:rocket-launch",
        is_on_fn=_has_active_booster,
        extra_attrs_fn=lambda l: l.offer.get("booster") if isinstance(l.offer.get("booster"), dict) else {},
    ),
    FreeMobileBinarySensorDescription(
        key="insurance_active",
        name="Assurance mobile",
        icon="mdi:shield-check",
        is_on_fn=lambda l: len(l.insurances) > 0,
        extra_attrs_fn=lambda l: {"insurances": l.insurances} if l.insurances else {},
    ),
    FreeMobileBinarySensorDescription(
        key="proxi_eligible",
        name="Free Proxi éligible",
        icon="mdi:headset",
        is_on_fn=lambda l: bool(l.proxi_eligibility.get("is_eligible", False)),
        extra_attrs_fn=lambda l: {
            "has_contact_open": l.proxi_eligibility.get("has_contact_open"),
            "web_url": l.proxi_eligibility.get("web_url"),
        }
        if l.proxi_eligibility
        else {},
    ),
    FreeMobileBinarySensorDescription(
        key="retractable",
        name="Délai de rétractation actif",
        icon="mdi:calendar-alert",
        is_on_fn=lambda l: bool(l.retractation.get("retractable", False)),
        extra_attrs_fn=lambda l: {
            "last_retractable_date": l.retractation.get("lastRetractableDate"),
            "items": l.retractation.get("items"),
        }
        if l.retractation
        else {},
    ),
    FreeMobileBinarySensorDescription(
        key="flex_eligible",
        name="Éligible Free Flex",
        icon="mdi:cellphone-check",
        is_on_fn=lambda l: l.is_flex_eligible,
    ),
    FreeMobileBinarySensorDescription(
        key="watch_eligible",
        name="Éligible eSIM Watch",
        icon="mdi:watch",
        is_on_fn=lambda l: l.is_watch_eligible,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Free Mobile binary sensors from a config entry."""
    coordinator: FreeMobileDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        FreeMobileBinarySensorEntity(coordinator, login, desc)
        for login in coordinator.data
        for desc in BINARY_SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class FreeMobileBinarySensorEntity(
    CoordinatorEntity[FreeMobileDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of a Free Mobile line binary sensor."""

    entity_description: FreeMobileBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FreeMobileDataUpdateCoordinator,
        login: str,
        description: FreeMobileBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._login = login
        self.entity_description = description
        self._attr_unique_id = f"{login}_{description.key}"
        line = coordinator.data[login]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, login)},
            name=f"Free Mobile {line.msisdn}",
            manufacturer=MANUFACTURER,
        )

    @property
    def is_on(self) -> bool | None:
        line = self.coordinator.data.get(self._login)
        if line is None:
            return None
        return self.entity_description.is_on_fn(line)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.extra_attrs_fn:
            return None
        line = self.coordinator.data.get(self._login)
        if line is None:
            return None
        return self.entity_description.extra_attrs_fn(line)
