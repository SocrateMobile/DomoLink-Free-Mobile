"""DataUpdateCoordinator for DomoLink Free Mobile."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FreeMobileApiClient, FreeMobileApiError, FreeMobileAuthError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class FreeMobileLineData:
    """Aggregated state for one Free Mobile subscriber line."""

    login: str
    msisdn: str
    plan_type: str
    state: str
    has_freebox_advantage: bool
    is_master: bool
    consumption: dict[str, Any] = field(default_factory=dict)
    offer: dict[str, Any] = field(default_factory=dict)
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    invoices: dict[str, Any] = field(default_factory=dict)
    unpaids: dict[str, Any] = field(default_factory=dict)
    sims: list[dict[str, Any]] = field(default_factory=list)


class FreeMobileDataUpdateCoordinator(DataUpdateCoordinator[dict[str, FreeMobileLineData]]):
    """Coordinator polling Free Mobile lines every 15 minutes."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: FreeMobileApiClient,
        main_login: str,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{main_login}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.main_login = main_login
        self.entry = entry

    async def _async_fetch_line(self, line_meta: dict[str, Any]) -> FreeMobileLineData:
        login = str(line_meta.get("login") or self.main_login)
        consumption, offer, options_list, invoices, unpaids, sims = await asyncio.gather(
            self.client.async_get_consumption(login),
            self.client.async_get_offer(login),
            self.client.async_get_options(login),
            self.client.async_get_invoices(login),
            self.client.async_get_unpaids(login),
            self.client.async_get_sims(login),
        )
        options_map = {
            str(opt["id"]): opt
            for opt in options_list
            if isinstance(opt, dict) and opt.get("id")
        }
        return FreeMobileLineData(
            login=login,
            msisdn=str(line_meta.get("msisdn") or login),
            plan_type=str(line_meta.get("planType") or offer.get("planType") or ""),
            state=str(line_meta.get("state") or "active"),
            has_freebox_advantage=bool(line_meta.get("hasFreeboxAdvantage", False)),
            is_master=bool(line_meta.get("isMaster", login == self.main_login)),
            consumption=consumption if isinstance(consumption, dict) else {},
            offer=offer if isinstance(offer, dict) else {},
            options=options_map,
            invoices=invoices if isinstance(invoices, dict) else {},
            unpaids=unpaids if isinstance(unpaids, dict) else {},
            sims=sims if isinstance(sims, list) else [],
        )

    async def _async_update_data(self) -> dict[str, FreeMobileLineData]:
        try:
            subscriber = await self.client.async_get_subscriber(self.main_login)
            lines = subscriber.get("lines") or [
                {
                    "login": self.main_login,
                    "msisdn": self.main_login,
                    "hasFreeboxAdvantage": subscriber.get("hasFreeboxAdvantage", False),
                    "state": "active",
                    "isMaster": True,
                }
            ]
            active_lines = [
                line for line in lines if str(line.get("state", "active")).lower() == "active"
            ] or lines

            results = await asyncio.gather(
                *(self._async_fetch_line(line) for line in active_lines)
            )
            return {line.login: line for line in results}
        except FreeMobileAuthError as err:
            raise ConfigEntryAuthFailed(f"Free Mobile authentication failed: {err}") from err
        except FreeMobileApiError as err:
            raise UpdateFailed(f"Error updating Free Mobile data: {err}") from err
