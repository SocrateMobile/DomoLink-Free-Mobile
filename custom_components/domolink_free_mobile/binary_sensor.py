"""Binary sensor platform for DomoLink Free Mobile."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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


@dataclass(frozen=True, kw_only=True)
class FreeMobileBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Free Mobile binary sensor."""

    is_on_fn: Callable[[FreeMobileLineData], bool]


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
