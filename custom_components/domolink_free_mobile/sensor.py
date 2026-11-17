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


@dataclass(frozen=True, kw_only=True)
class FreeMobileSensorDescription(SensorEntityDescription):
    """Describes a Free Mobile line sensor."""

    value_fn: Callable[[FreeMobileLineData], Any]


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
        key="national_sms",
        name="SMS nationaux",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:message-text",
        value_fn=lambda l: l.consumption.get("national", {}).get("consumption", {}).get("sms"),
    ),
    FreeMobileSensorDescription(
        key="national_mms",
        name="MMS nationaux",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:message-image",
        value_fn=lambda l: l.consumption.get("national", {}).get("consumption", {}).get("mms"),
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
    ),
    FreeMobileSensorDescription(
        key="offer_name",
        name="Forfait",
        icon="mdi:sim",
        value_fn=lambda l: l.offer.get("name") or l.plan_type,
    ),
    FreeMobileSensorDescription(
        key="last_invoice",
        name="Dernière facture",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:receipt-text",
        value_fn=_last_invoice_amount,
    ),
    FreeMobileSensorDescription(
        key="unpaid_amount",
        name="Montant impayé",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:alert-circle-outline",
        value_fn=_unpaid_total,
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
