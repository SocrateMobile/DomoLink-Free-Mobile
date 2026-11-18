"""Sensor platform for DomoLink Free Mobile."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FreeMobileDataUpdateCoordinator, FreeMobileLineData


def _bytes_to_gb(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return round(float(raw) / (1024**3), 3)
    except (TypeError, ValueError):
        return None


def _seconds_to_min(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return round(float(raw) / 60.0, 1)
    except (TypeError, ValueError):
        return None


def _sum_billing(section: dict[str, Any], keys: tuple[str, ...]) -> float:
    total = 0.0
    for key in keys:
        val = section.get(key)
        if val is not None:
            try:
                total += float(val)
            except (TypeError, ValueError):
                continue
    return round(total, 2)


def _out_of_bundle_total(line: FreeMobileLineData) -> float:
    nat = line.consumption.get("national", {}).get("billing", {}) or {}
    roam = line.consumption.get("roaming", {}).get("billing", {}) or {}
    nat_sum = _sum_billing(
        nat,
        (
            "data",
            "sms",
            "mms",
            "smsPlus",
            "nationalVoice",
            "internationalVoice",
            "nationalSpecialNumbersVoice",
            "internationalSpecialNumbersVoice",
        ),
    )
    roam_sum = _sum_billing(
        roam,
        ("data", "sms", "mms", "roamingVoice", "roamingOfflineModeVoice"),
    )
    return round(nat_sum + roam_sum, 2)


def _unpaid_total(line: FreeMobileLineData) -> float:
    total = 0.0
    for bucket in ("personalUnpaids", "parentUnpaids", "childrenUnpaids"):
        for item in line.unpaids.get(bucket, []) or []:
            if isinstance(item, dict) and item.get("amount") is not None:
                try:
                    total += float(item["amount"])
                except (TypeError, ValueError):
                    continue
    return round(total, 2)


def _last_invoice_amount(line: FreeMobileLineData) -> float | None:
    invoices = line.invoices.get("invoices") or []
    if not invoices:
        return None
    latest = invoices[0]
    amt = latest.get("amount")
    if amt is None:
        return None
    try:
        # API returns cents on Long amount fields when integer, or EUR float
        val = float(amt)
        return round(val / 100.0, 2) if isinstance(amt, int) and val >= 100 else round(val, 2)
    except (TypeError, ValueError):
        return None


def _quota_to_gb(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return 0.0
    if val >= 1024**2:
        return round(val / (1024**3), 2)
    if val >= 1000:
        return round(val / 1024.0, 2)
    return round(val, 2)


def _national_quota_gb(line: FreeMobileLineData) -> float | None:
    nat = line.offer.get("nationalData") or {}
    if line.has_freebox_advantage and nat.get("fairuseFreebox") is not None:
        return _quota_to_gb(nat.get("fairuseFreebox"))
    return _quota_to_gb(nat.get("fairuse"))


def _national_remaining_gb(line: FreeMobileLineData) -> float | None:
    quota = _national_quota_gb(line)
    used = _bytes_to_gb(line.consumption.get("national", {}).get("consumption", {}).get("data"))
    if quota is None or used is None or quota <= 0:
        return None
    return round(max(0.0, quota - used), 3)


def _national_usage_pct(line: FreeMobileLineData) -> float | None:
    quota = _national_quota_gb(line)
    used = _bytes_to_gb(line.consumption.get("national", {}).get("consumption", {}).get("data"))
    if quota is None or used is None or quota <= 0:
        return None
    return round(min(100.0, (used / quota) * 100.0), 1)


def _roaming_quota_gb(line: FreeMobileLineData) -> float | None:
    roam = line.offer.get("roamingData") or {}
    return _quota_to_gb(roam.get("fairuse"))


def _offer_price_eur(line: FreeMobileLineData) -> float | None:
    raw = line.offer.get("price")
    if raw is None:
        return None
    try:
        cleaned = str(raw).replace("€", "").replace(",", ".").strip()
        return round(float(cleaned), 2)
    except (TypeError, ValueError):
        return None


def _active_services_count(line: FreeMobileLineData) -> int:
    return sum(1 for svc in line.services if isinstance(svc, dict) and svc.get("isActive"))


def _sim_type(line: FreeMobileLineData) -> str | None:
    if not line.sims:
        return None
    active = next(
        (s for s in line.sims if isinstance(s, dict) and str(s.get("state", "")).lower() == "active"),
        line.sims[0],
    )
    return active.get("simCardType") or active.get("origin") or active.get("state")


def _communications_count(line: FreeMobileLineData) -> int:
    total = 0
    for cat in ("mail", "sms"):
        cat_data = line.communications.get(cat)
        if isinstance(cat_data, dict):
            if isinstance(cat_data.get("totalItems"), int):
                total += cat_data["totalItems"]
            elif isinstance(cat_data.get("items"), list):
                total += len(cat_data["items"])
    return total


@dataclass(frozen=True, kw_only=True)
class FreeMobileSensorDescription(SensorEntityDescription):
    """Describes a Free Mobile line sensor."""

    value_fn: Callable[[FreeMobileLineData], Any]
    extra_attrs_fn: Callable[[FreeMobileLineData], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[FreeMobileSensorDescription, ...] = (
    FreeMobileSensorDescription(
        key="national_data",
        name="Data nationale consommée",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cellular",
        value_fn=lambda l: _bytes_to_gb(
            l.consumption.get("national", {}).get("consumption", {}).get("data")
        ),
    ),
    FreeMobileSensorDescription(
        key="national_data_quota",
        name="Quota Data nationale",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        icon="mdi:database-outline",
        value_fn=_national_quota_gb,
    ),
    FreeMobileSensorDescription(
        key="national_data_remaining",
        name="Data nationale restante",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        icon="mdi:database-clock-outline",
        value_fn=_national_remaining_gb,
    ),
    FreeMobileSensorDescription(
        key="national_data_usage_pct",
        name="Utilisation Data nationale",
        native_unit_of_measurement="%",
        icon="mdi:percent-outline",
        value_fn=_national_usage_pct,
    ),
    FreeMobileSensorDescription(
        key="roaming_data",
        name="Data roaming consommée",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:earth",
        value_fn=lambda l: _bytes_to_gb(
            l.consumption.get("roaming", {}).get("consumption", {}).get("data")
        ),
    ),
    FreeMobileSensorDescription(
        key="roaming_data_quota",
        name="Quota Data roaming",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        icon="mdi:earth-box",
        value_fn=_roaming_quota_gb,
        extra_attrs_fn=lambda l: {
            "over_fairuse_mo_price": (l.offer.get("roamingData") or {}).get("overFairuseMoPrice")
        },
    ),
    FreeMobileSensorDescription(
        key="national_voice",
        name="Appels nationaux",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:phone",
        value_fn=lambda l: _seconds_to_min(
            l.consumption.get("national", {})
            .get("consumption", {})
            .get("voice", {})
            .get("nationalVoiceTime")
        ),
    ),
    FreeMobileSensorDescription(
        key="international_voice",
        name="Appels internationaux",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:phone-outgoing",
        value_fn=lambda l: _seconds_to_min(
            l.consumption.get("national", {})
            .get("consumption", {})
            .get("voice", {})
            .get("internationalVoiceTime")
        ),
    ),
    FreeMobileSensorDescription(
        key="national_special_voice",
        name="Appels numéros spéciaux",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:phone-alert",
        value_fn=lambda l: _seconds_to_min(
            l.consumption.get("national", {})
            .get("consumption", {})
            .get("voice", {})
            .get("nationalSpecialNumbersVoiceTime")
        ),
    ),
    FreeMobileSensorDescription(
        key="roaming_voice_out",
        name="Appels roaming émis",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:phone-roaming",
        value_fn=lambda l: _seconds_to_min(
            l.consumption.get("roaming", {})
            .get("consumption", {})
            .get("voice", {})
            .get("roamingOutgoingVoiceTime")
        ),
    ),
    FreeMobileSensorDescription(
        key="roaming_voice_in",
        name="Appels roaming reçus",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:phone-incoming",
        value_fn=lambda l: _seconds_to_min(
            l.consumption.get("roaming", {})
            .get("consumption", {})
            .get("voice", {})
            .get("roamingIncomingVoiceTime")
        ),
    ),
    FreeMobileSensorDescription(
        key="national_sms",
        name="SMS nationaux",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:message-text",
        value_fn=lambda l: l.consumption.get("national", {}).get("consumption", {}).get("sms"),
    ),
    FreeMobileSensorDescription(
        key="national_sms_plus",
        name="SMS+ nationaux",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:message-plus",
        value_fn=lambda l: l.consumption.get("national", {}).get("consumption", {}).get("smsPlus"),
    ),
    FreeMobileSensorDescription(
        key="national_mms",
        name="MMS nationaux",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:message-image",
        value_fn=lambda l: l.consumption.get("national", {}).get("consumption", {}).get("mms"),
    ),
    FreeMobileSensorDescription(
        key="roaming_sms",
        name="SMS roaming",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:message-text-outline",
        value_fn=lambda l: l.consumption.get("roaming", {}).get("consumption", {}).get("sms"),
    ),
    FreeMobileSensorDescription(
        key="roaming_mms",
        name="MMS roaming",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:message-image-outline",
        value_fn=lambda l: l.consumption.get("roaming", {}).get("consumption", {}).get("mms"),
    ),
    FreeMobileSensorDescription(
        key="out_of_bundle",
        name="Hors-forfait",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:currency-eur",
        value_fn=_out_of_bundle_total,
    ),
    FreeMobileSensorDescription(
        key="reset_period",
        name="Date remise à zéro",
        icon="mdi:calendar-refresh",
        value_fn=lambda l: l.consumption.get("resetPeriod") or l.consumption.get("endPeriod"),
        extra_attrs_fn=lambda l: {
            "start_period": l.consumption.get("startPeriod"),
            "end_period": l.consumption.get("endPeriod"),
        },
    ),
    FreeMobileSensorDescription(
        key="offer_name",
        name="Forfait",
        icon="mdi:sim",
        value_fn=lambda l: l.offer.get("name") or l.plan_type,
        extra_attrs_fn=lambda l: {
            "msisdn": l.msisdn,
            "offer_id": l.offer_id,
            "plan_type": l.plan_type,
            "start_date": l.offer.get("startDate"),
            "end_date": l.offer.get("endDate"),
            "subscription_date": l.subscription_date,
            "detail_plan_url": l.plan_info.get("detailPlanUrl"),
            "pricing_page_url": l.plan_info.get("pricingPageUrl"),
        },
    ),
    FreeMobileSensorDescription(
        key="offer_price",
        name="Prix du forfait",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:cash",
        value_fn=_offer_price_eur,
    ),
    FreeMobileSensorDescription(
        key="last_invoice",
        name="Dernière facture",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:receipt-text",
        value_fn=_last_invoice_amount,
        extra_attrs_fn=lambda l: {
            k: v
            for k, v in ((l.invoices.get("invoices") or [{}])[0]).items()
            if k in ("id", "name", "date", "state", "fileState")
        }
        if l.invoices.get("invoices")
        else {},
    ),
    FreeMobileSensorDescription(
        key="unpaid_amount",
        name="Montant impayé",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:alert-circle-outline",
        value_fn=_unpaid_total,
    ),
    FreeMobileSensorDescription(
        key="active_services",
        name="Services actifs",
        icon="mdi:cog-outline",
        value_fn=_active_services_count,
        extra_attrs_fn=lambda l: {
            "active_services": [s.get("name") for s in l.services if isinstance(s, dict) and s.get("isActive")],
            "inactive_services": [s.get("name") for s in l.services if isinstance(s, dict) and not s.get("isActive")],
        },
    ),
    FreeMobileSensorDescription(
        key="sim_type",
        name="Type de SIM",
        icon="mdi:sim-outline",
        value_fn=_sim_type,
        extra_attrs_fn=lambda l: {
            "sims": [
                {
                    "iccid": s.get("iccid"),
                    "type": s.get("simCardType"),
                    "state": s.get("state"),
                    "activable": s.get("activable"),
                    "created_at": s.get("createdAt"),
                }
                for s in l.sims
                if isinstance(s, dict)
            ]
        },
    ),
    FreeMobileSensorDescription(
        key="puk_code",
        name="Code PUK",
        icon="mdi:lock-reset",
        value_fn=lambda l: l.puk,
    ),
    FreeMobileSensorDescription(
        key="banners_count",
        name="Alertes compte",
        icon="mdi:bell-badge-outline",
        value_fn=lambda l: len(l.banners),
        extra_attrs_fn=lambda l: {
            "banners": [
                {"title": b.get("title"), "description": b.get("description"), "tag": b.get("tag")}
                for b in l.banners
                if isinstance(b, dict)
            ]
        },
    ),
    FreeMobileSensorDescription(
        key="communications_count",
        name="Communications récentes",
        icon="mdi:email-multiple-outline",
        value_fn=_communications_count,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Free Mobile sensor entities from a config entry."""
    coordinator: FreeMobileDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        FreeMobileSensorEntity(coordinator, login, desc)
        for login in coordinator.data
        for desc in SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class FreeMobileSensorEntity(CoordinatorEntity[FreeMobileDataUpdateCoordinator], SensorEntity):
    """Representation of a Free Mobile line sensor."""

    entity_description: FreeMobileSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FreeMobileDataUpdateCoordinator,
        login: str,
        description: FreeMobileSensorDescription,
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
            model=line.offer.get("name") or line.plan_type or "Forfait Free Mobile",
        )

    @property
    def native_value(self) -> Any:
        line = self.coordinator.data.get(self._login)
        if line is None:
            return None
        return self.entity_description.value_fn(line)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.extra_attrs_fn:
            return None
        line = self.coordinator.data.get(self._login)
        if line is None:
            return None
        return self.entity_description.extra_attrs_fn(line)
