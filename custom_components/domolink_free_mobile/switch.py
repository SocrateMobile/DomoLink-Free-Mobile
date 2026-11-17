"""Switch platform for Free Mobile line options."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROLLABLE_OPTION_IDS, DOMAIN, MANUFACTURER, OPTION_LABELS
from .coordinator import FreeMobileDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Free Mobile option switches."""
    coordinator: FreeMobileDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[FreeMobileOptionSwitch] = []
    for login, line in coordinator.data.items():
        for opt_id in CONTROLLABLE_OPTION_IDS:
            opt = line.options.get(opt_id)
            if opt and opt.get("available", True) and not opt.get("included", False):
                entities.append(FreeMobileOptionSwitch(coordinator, login, opt_id))
    async_add_entities(entities)


class FreeMobileOptionSwitch(
    CoordinatorEntity[FreeMobileDataUpdateCoordinator], SwitchEntity
):
    """Switch controlling a Free Mobile subscriber option."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FreeMobileDataUpdateCoordinator,
        login: str,
        option_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._login = login
        self._option_id = option_id
        self._attr_unique_id = f"{login}_option_{option_id}"
        line = coordinator.data[login]
        opt = line.options.get(option_id, {})
        self._attr_name = opt.get("name") or OPTION_LABELS.get(option_id, option_id)
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
        opt = line.options.get(self._option_id)
        if opt is None:
            return None
        return bool(opt.get("active", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.async_set_option(self._login, self._option_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.async_set_option(self._login, self._option_id, False)
        await self.coordinator.async_request_refresh()
