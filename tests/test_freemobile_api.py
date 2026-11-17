"""Unit tests for DomoLink Free Mobile API client and sensor/coordinator helpers."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock

import pytest

# Stub minimal homeassistant modules so package imports cleanly without HA installed
ha_pkg = types.ModuleType("homeassistant")
ha_const = types.ModuleType("homeassistant.const")
ha_const.Platform = types.SimpleNamespace(SENSOR="sensor", BINARY_SENSOR="binary_sensor", SWITCH="switch")
ha_const.CONF_PASSWORD = "password"
ha_const.CONF_USERNAME = "username"
ha_const.UnitOfInformation = types.SimpleNamespace(GIGABYTES="GB")
ha_const.UnitOfTime = types.SimpleNamespace(MINUTES="min")

ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object

ha_ce = types.ModuleType("homeassistant.config_entries")
ha_ce.ConfigEntry = object
ha_ce.ConfigFlow = type("ConfigFlow", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)})
ha_ce.ConfigFlowResult = dict

ha_exc = types.ModuleType("homeassistant.exceptions")
ha_exc.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})

ha_helpers = types.ModuleType("homeassistant.helpers")
ha_aio = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aio.async_get_clientsession = lambda hass: None
ha_dr = types.ModuleType("homeassistant.helpers.device_registry")
ha_dr.DeviceInfo = dict
ha_ep = types.ModuleType("homeassistant.helpers.entity_platform")
ha_ep.AddEntitiesCallback = object
ha_uc = types.ModuleType("homeassistant.helpers.update_coordinator")
ha_uc.DataUpdateCoordinator = type("DataUpdateCoordinator", (), {"__class_getitem__": classmethod(lambda cls, item: cls), "__init__": lambda self, *a, **kw: None})
ha_uc.CoordinatorEntity = type("CoordinatorEntity", (), {"__class_getitem__": classmethod(lambda cls, item: cls), "__init__": lambda self, coord: setattr(self, "coordinator", coord)})
ha_uc.UpdateFailed = type("UpdateFailed", (Exception,), {})

ha_comp = types.ModuleType("homeassistant.components")
from dataclasses import dataclass
ha_sensor = types.ModuleType("homeassistant.components.sensor")
@dataclass(frozen=True, kw_only=True)
class _StubSensorEntityDescription:
    key: str
    name: str | None = None
    native_unit_of_measurement: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None

ha_sensor.SensorDeviceClass = types.SimpleNamespace(DATA_SIZE="data_size", DURATION="duration", MONETARY="monetary")
ha_sensor.SensorEntity = object
ha_sensor.SensorEntityDescription = _StubSensorEntityDescription
ha_sensor.SensorStateClass = types.SimpleNamespace(TOTAL_INCREASING="total_increasing", TOTAL="total")

for name, mod in [
    ("homeassistant", ha_pkg),
    ("homeassistant.const", ha_const),
    ("homeassistant.core", ha_core),
    ("homeassistant.config_entries", ha_ce),
    ("homeassistant.exceptions", ha_exc),
    ("homeassistant.helpers", ha_helpers),
    ("homeassistant.helpers.aiohttp_client", ha_aio),
    ("homeassistant.helpers.device_registry", ha_dr),
    ("homeassistant.helpers.entity_platform", ha_ep),
    ("homeassistant.helpers.update_coordinator", ha_uc),
    ("homeassistant.components", ha_comp),
    ("homeassistant.components.sensor", ha_sensor),
]:
    sys.modules.setdefault(name, mod)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.domolink_free_mobile.api import (  # noqa: E402
    FreeMobileApiClient,
    FreeMobileAuthError,
    FreeMobileOtpRequired,
)
from custom_components.domolink_free_mobile.coordinator import FreeMobileLineData  # noqa: E402
from custom_components.domolink_free_mobile.sensor import (  # noqa: E402
    _last_invoice_amount,
    _out_of_bundle_total,
    _unpaid_total,
)


class FakeResponse:
    def __init__(self, status: int, json_data=None, text_data: str = "") -> None:
        self.status = status
        self._json = json_data
        self._text = text_data
        self.content_type = "application/json" if json_data is not None else "text/plain"

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        resp = self._responses.pop(0)

        @asynccontextmanager
        async def _cm():
            yield resp

        return _cm()


def test_login_otp_required_and_validate():
    session = FakeSession([
        FakeResponse(200, {"otpId": 42, "type2FA": "sms", "trustUuid": "uuid-1", "accessToken": "temp-otp-jwt"}),
        FakeResponse(200, {"accessToken": "acc-1", "refreshToken": "ref-1", "trustUuid": "uuid-2"}),
    ])
    client = FreeMobileApiClient(session)

    with pytest.raises(FreeMobileOtpRequired) as exc:
        asyncio.run(client.async_login("12345678", "secret"))
    assert exc.value.otp_id == 42
    assert exc.value.type_2fa == "sms"
    assert exc.value.access_token == "temp-otp-jwt"

    res = asyncio.run(client.async_validate_otp(42, 654321, is_trusted=True))
    assert res["accessToken"] == "acc-1"
    assert client.access_token == "acc-1"
    assert client.refresh_token == "ref-1"
    assert client.trust_uuid == "uuid-2"
    assert session.calls[1][2]["headers"]["Authorization"] == "Bearer temp-otp-jwt"


def test_401_triggers_single_refresh_and_retries():
    refreshed_calls = []
    session = FakeSession([
        FakeResponse(401, text_data="expired"),
        FakeResponse(200, {"accessToken": "acc-new", "refreshToken": "ref-new"}),
        FakeResponse(200, {"national": {"consumption": {"data": 1073741824}}}),
    ])
    client = FreeMobileApiClient(
        session,
        access_token="acc-old",
        refresh_token="ref-old",
        on_tokens_refreshed=lambda a, r: refreshed_calls.append((a, r)),
    )
    data = asyncio.run(client.async_get_consumption("12345678"))
    assert data["national"]["consumption"]["data"] == 1073741824
    assert refreshed_calls == [("acc-new", "ref-new")]
    assert len(session.calls) == 3
    assert session.calls[1][0] == "POST"
    assert "refreshToken" in session.calls[1][1]


def test_set_option_uses_post_and_put():
    session = FakeSession([
        FakeResponse(200, {"success": True}),
        FakeResponse(200, {"success": True}),
    ])
    client = FreeMobileApiClient(session, access_token="tok")
    assert asyncio.run(client.async_set_option("12345678", "_5g", True)) is True
    assert asyncio.run(client.async_set_option("12345678", "_5g", False)) is True
    assert session.calls[0][0] == "POST"
    assert session.calls[0][1].endswith("/options/activate")
    assert session.calls[1][0] == "PUT"
    assert session.calls[1][1].endswith("/options/desactivate")


def test_invalid_auth_without_refresh_raises():
    session = FakeSession([FakeResponse(401, text_data="bad creds")])
    client = FreeMobileApiClient(session)
    with pytest.raises(FreeMobileAuthError):
        asyncio.run(client.async_login("12345678", "wrong"))


def test_sensor_calculations():
    line = FreeMobileLineData(
        login="12345678",
        msisdn="0601020304",
        plan_type="20",
        state="active",
        has_freebox_advantage=True,
        is_master=True,
        consumption={
            "national": {"billing": {"data": 1.5, "smsPlus": 0.5}},
            "roaming": {"billing": {"roamingVoice": 2.25}},
        },
        invoices={"invoices": [{"amount": 1599}]},
        unpaids={"personalUnpaids": [{"amount": 19.99}], "parentUnpaids": []},
    )
    assert _out_of_bundle_total(line) == 4.25
    assert _last_invoice_amount(line) == 15.99
    assert _unpaid_total(line) == 19.99
