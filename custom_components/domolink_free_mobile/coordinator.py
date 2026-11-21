"""DataUpdateCoordinator for DomoLink Free Mobile."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
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
    offer_id: int | None = None
    subscription_date: str | None = None
    is_flex_eligible: bool = False
    is_watch_eligible: bool = False
    contact: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)
    debit_method: dict[str, Any] = field(default_factory=dict)
    consumption: dict[str, Any] = field(default_factory=dict)
    offer: dict[str, Any] = field(default_factory=dict)
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    invoices: dict[str, Any] = field(default_factory=dict)
    unpaids: dict[str, Any] = field(default_factory=dict)
    sims: list[dict[str, Any]] = field(default_factory=list)
    puk: str | None = None
    services: list[dict[str, Any]] = field(default_factory=list)
    insurances: list[dict[str, Any]] = field(default_factory=list)
    plan_info: dict[str, Any] = field(default_factory=dict)
    proxi_eligibility: dict[str, Any] = field(default_factory=dict)
    retractation: dict[str, Any] = field(default_factory=dict)
    banners: list[dict[str, Any]] = field(default_factory=list)
    communications: dict[str, Any] = field(default_factory=dict)


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
        self._puk_cache: dict[str, str | None] = {}

    async def _safe_fetch(self, coro: Any, default: Any) -> Any:
        try:
            return await coro
        except FreeMobileAuthError:
            raise
        except FreeMobileApiError as err:
            _LOGGER.debug("Optional Free Mobile endpoint failed: %s", err)
            return default

    async def _async_fetch_line(
        self, line_meta: dict[str, Any], debit_method: dict[str, Any] | None = None
    ) -> FreeMobileLineData:
        login = str(line_meta.get("login") or self.main_login)
        raw_offer_id = line_meta.get("offerId")
        offer_id = int(raw_offer_id) if raw_offer_id is not None else None

        (
            consumption,
            offer,
            options_list,
            invoices,
            unpaids,
            sims,
            services,
            insurances,
            plan_info,
            proxi_eligibility,
            retractation,
            banners,
            communications,
        ) = await asyncio.gather(
            self.client.async_get_consumption(login),
            self.client.async_get_offer(login),
            self.client.async_get_options(login),
            self.client.async_get_invoices(login),
            self.client.async_get_unpaids(login),
            self.client.async_get_sims(login),
            self._safe_fetch(self.client.async_get_services(login), []),
            self._safe_fetch(self.client.async_get_insurances(login), []),
            self._safe_fetch(self.client.async_get_plan_info(login), {}),
            self._safe_fetch(self.client.async_get_proxi_eligibility(login), {}),
            self._safe_fetch(self.client.async_get_retractation(login), {}),
            self._safe_fetch(self.client.async_get_banners(login), []),
            self._safe_fetch(self.client.async_get_communications(login), {}),
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
            offer_id=offer_id,
            subscription_date=line_meta.get("subscriptionDate"),
            is_flex_eligible=bool(line_meta.get("isFlexEligible", False)),
            is_watch_eligible=bool(line_meta.get("isWatchEligible", False)),
            contact=line_meta.get("contact") if isinstance(line_meta.get("contact"), dict) else {},
            identity=line_meta.get("identity") if isinstance(line_meta.get("identity"), dict) else {},
            debit_method=debit_method if isinstance(debit_method, dict) else {},
            consumption=consumption if isinstance(consumption, dict) else {},
            offer=offer if isinstance(offer, dict) else {},
            options=options_map,
            invoices=invoices if isinstance(invoices, dict) else {},
            unpaids=unpaids if isinstance(unpaids, dict) else {},
            sims=sims if isinstance(sims, list) else [],
            puk=self._puk_cache.get(login),
            services=services if isinstance(services, list) else [],
            insurances=insurances if isinstance(insurances, list) else [],
            plan_info=plan_info if isinstance(plan_info, dict) else {},
            proxi_eligibility=proxi_eligibility if isinstance(proxi_eligibility, dict) else {},
            retractation=retractation if isinstance(retractation, dict) else {},
            banners=banners if isinstance(banners, list) else [],
            communications=communications if isinstance(communications, dict) else {},
        )

    async def _async_update_data(self) -> dict[str, FreeMobileLineData]:
        try:
            subscriber = await self.client.async_get_subscriber(self.main_login)
            debit_method = subscriber.get("debitMethod")
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
                *(self._async_fetch_line(line, debit_method=debit_method) for line in active_lines)
            )
            return {line.login: line for line in results}
        except FreeMobileAuthError as err:
            raise ConfigEntryAuthFailed(f"Free Mobile authentication failed: {err}") from err
        except FreeMobileApiError as err:
            raise UpdateFailed(f"Error updating Free Mobile data: {err}") from err

    async def async_request_puk(self, login: str) -> str | None:
        """Fetch the PUK code on demand for a line and update its cached state."""
        data = self.data or {}
        line = data.get(login)
        if line is None or line.offer_id is None:
            raise HomeAssistantError(
                "Impossible de récupérer le code PUK : identifiant d'offre introuvable."
            )

        try:
            puk = await self.client.async_get_puk(line.offer_id)
        except FreeMobileAuthError as err:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                "La session Free Mobile a expiré. Veuillez vous ré-authentifier."
            ) from err
        except FreeMobileApiError as err:
            raise HomeAssistantError(
                f"Erreur lors de la récupération du code PUK : {err}"
            ) from err

        self._puk_cache[login] = puk
        line.puk = puk
        self.async_set_updated_data(data)
        return puk
