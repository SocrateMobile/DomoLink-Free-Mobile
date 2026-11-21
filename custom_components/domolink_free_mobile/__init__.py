"""The DomoLink Free Mobile integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FreeMobileApiClient
from .const import CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_TRUST_UUID, DOMAIN
from .coordinator import FreeMobileDataUpdateCoordinator

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DomoLink Free Mobile from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    def _persist_tokens(new_access: str, new_refresh: str) -> None:
        updated = dict(entry.data)
        updated[CONF_ACCESS_TOKEN] = new_access
        updated[CONF_REFRESH_TOKEN] = new_refresh
        hass.config_entries.async_update_entry(entry, data=updated)

    client = FreeMobileApiClient(
        session=async_get_clientsession(hass),
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        trust_uuid=entry.data.get(CONF_TRUST_UUID),
        on_tokens_refreshed=_persist_tokens,
    )
    coordinator = FreeMobileDataUpdateCoordinator(
        hass=hass,
        client=client,
        main_login=str(entry.data[CONF_USERNAME]),
        entry=entry,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
