"""Button platform for DomoLink Free Mobile."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FreeMobileDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Free Mobile button entities from a config entry."""
    coordinator: FreeMobileDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        FreeMobileRequestPukButton(coordinator, login)
        for login in coordinator.data
    ]
    async_add_entities(entities)


class FreeMobileRequestPukButton(
    CoordinatorEntity[FreeMobileDataUpdateCoordinator], ButtonEntity
):
    """Button to explicitly request and display the SIM PUK code."""

    _attr_has_entity_name = True
    _attr_name = "Obtenir le code PUK"
    _attr_icon = "mdi:lock-reset"

    def __init__(
        self,
        coordinator: FreeMobileDataUpdateCoordinator,
        login: str,
    ) -> None:
        super().__init__(coordinator)
        self._login = login
        self._attr_unique_id = f"{login}_request_puk"
        line = coordinator.data[login]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, login)},
            name=f"Free Mobile {line.msisdn}",
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        line = self.coordinator.data.get(self._login)
        return line is not None and line.offer_id is not None

    async def async_press(self) -> None:
        """Request the PUK code on demand and update the PUK sensor."""
        await self.coordinator.async_request_puk(self._login)
