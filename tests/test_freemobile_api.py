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
ha_const.Platform = types.SimpleNamespace(SENSOR="sensor", BINARY_SENSOR="binary_sensor", SWITCH="switch", BUTTON="button")
ha_const.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic", CONFIG="config")
ha_const.CONF_PASSWORD = "password"
ha_const.CONF_USERNAME = "username"
ha_const.UnitOfInformation = types.SimpleNamespace(GIGABYTES="GB")
ha_const.UnitOfTime = types.SimpleNamespace(MINUTES="min")

ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object

ha_ce = types.ModuleType("homeassistant.config_entries")
ha_ce.SOURCE_REAUTH = "reauth"
ha_ce.ConfigEntry = object


class _StubConfigFlow:
    source = "user"
    hass = None
    context = {}

    def __init_subclass__(cls, **kw):
        pass

    async def async_set_unique_id(self, unique_id):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass

    def _get_reauth_entry(self):
        if self.source != "reauth":
            raise ValueError(f"Source is {self.source}, expected reauth")
        return getattr(self, "_stub_reauth_entry", None)

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_update_reload_and_abort(self, entry, **kwargs):
        return {"type": "abort", "reason": "reauth_successful", "entry": entry, **kwargs}


ha_ce.ConfigFlow = _StubConfigFlow
ha_ce.ConfigFlowResult = dict

ha_exc = types.ModuleType("homeassistant.exceptions")
ha_exc.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
ha_exc.HomeAssistantError = type("HomeAssistantError", (Exception,), {})

ha_helpers = types.ModuleType("homeassistant.helpers")
ha_aio = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aio.async_get_clientsession = lambda hass: None
ha_dr = types.ModuleType("homeassistant.helpers.device_registry")
ha_dr.DeviceInfo = dict
ha_ep = types.ModuleType("homeassistant.helpers.entity_platform")
ha_ep.AddEntitiesCallback = object
ha_uc = types.ModuleType("homeassistant.helpers.update_coordinator")
ha_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator",
    (),
    {
        "__class_getitem__": classmethod(lambda cls, item: cls),
        "__init__": lambda self, *a, **kw: None,
        "async_update_listeners": lambda self: None,
    },
)
ha_uc.CoordinatorEntity = type(
    "CoordinatorEntity",
    (),
    {
        "__class_getitem__": classmethod(lambda cls, item: cls),
        "__init__": lambda self, coord: setattr(self, "coordinator", coord),
        "available": property(lambda self: True),
        "hass": None,
    },
)
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
    entity_registry_enabled_default: bool = True
    entity_category: str | None = None

ha_sensor.SensorDeviceClass = types.SimpleNamespace(DATA_SIZE="data_size", DURATION="duration", MONETARY="monetary")
ha_sensor.SensorEntity = object
ha_sensor.SensorEntityDescription = _StubSensorEntityDescription
ha_sensor.SensorStateClass = types.SimpleNamespace(TOTAL_INCREASING="total_increasing", TOTAL="total")

ha_button = types.ModuleType("homeassistant.components.button")
ha_button.ButtonEntity = object
ha_pn = types.ModuleType("homeassistant.components.persistent_notification")
ha_pn.async_create = MagicMock()
ha_comp.persistent_notification = ha_pn

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
    ("homeassistant.components.button", ha_button),
    ("homeassistant.components.persistent_notification", ha_pn),
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


def test_config_flow_user_otp_does_not_call_get_reauth_entry(monkeypatch):
    from custom_components.domolink_free_mobile import config_flow as cf_mod

    session = FakeSession([
        FakeResponse(200, {"otpId": 42, "type2FA": "email", "trustUuid": "uuid-1", "accessToken": "temp-otp"}),
        FakeResponse(200, {"accessToken": "acc-1", "refreshToken": "ref-1", "trustUuid": "uuid-2"}),
    ])
    monkeypatch.setattr(cf_mod, "async_get_clientsession", lambda hass: session)

    flow = cf_mod.FreeMobileConfigFlow()
    flow.source = "user"

    step1 = asyncio.run(flow.async_step_user({"username": "12345678", "password": "secret"}))
    assert step1["type"] == "form"
    assert step1["step_id"] == "otp"

    step2 = asyncio.run(flow.async_step_otp({"otp_code": "225760", "switch_email_otp": False}))
    assert step2["type"] == "create_entry"
    assert step2["title"] == "Free Mobile (12345678)"
    assert step2["data"]["access_token"] == "acc-1"
    assert step2["data"]["refresh_token"] == "ref-1"
    assert step2["data"]["trust_uuid"] == "uuid-2"


def test_config_flow_reauth_otp_updates_existing_entry(monkeypatch):
    from custom_components.domolink_free_mobile import config_flow as cf_mod

    session = FakeSession([
        FakeResponse(200, {"otpId": 99, "type2FA": "sms", "trustUuid": "uuid-old", "accessToken": "temp-otp"}),
        FakeResponse(200, {"accessToken": "acc-new", "refreshToken": "ref-new"}),
    ])
    monkeypatch.setattr(cf_mod, "async_get_clientsession", lambda hass: session)

    existing_entry = object()
    flow = cf_mod.FreeMobileConfigFlow()
    flow.source = "reauth"
    flow._stub_reauth_entry = existing_entry

    step1 = asyncio.run(flow.async_step_reauth({"username": "12345678", "trust_uuid": "uuid-old"}))
    assert step1["type"] == "form"
    assert step1["step_id"] == "reauth_confirm"

    step2 = asyncio.run(flow.async_step_reauth_confirm({"password": "secret"}))
    assert step2["type"] == "form"
    assert step2["step_id"] == "otp"

    step3 = asyncio.run(flow.async_step_otp({"otp_code": "123456", "switch_email_otp": False}))
    assert step3["type"] == "abort"
    assert step3["entry"] is existing_entry
    assert step3["data"]["access_token"] == "acc-new"
    assert step3["data"]["trust_uuid"] == "uuid-old"


def test_config_flow_otp_invalid_code_returns_form_error(monkeypatch):
    from custom_components.domolink_free_mobile import config_flow as cf_mod

    flow = cf_mod.FreeMobileConfigFlow()
    flow.source = "user"
    flow._otp_id = 42

    res = asyncio.run(flow.async_step_otp({"otp_code": "abc", "switch_email_otp": False}))
    assert res["type"] == "form"
    assert res["errors"]["base"] == "invalid_otp"


def test_new_apk_endpoints_and_helpers():
    from custom_components.domolink_free_mobile.sensor import (
        _active_services_count,
        _communications_count,
        _national_quota_gb,
        _national_remaining_gb,
        _national_usage_pct,
        _offer_price_eur,
        _sim_type,
    )

    session = FakeSession([
        FakeResponse(200, {"puk": "12345678"}),
        FakeResponse(200, {"services": [{"id": 1, "name": "VoLTE", "isActive": True}, {"id": 2, "name": "VVM", "isActive": False}]}),
        FakeResponse(200, {"insurances": [{"name": "Assurance Vol"}]}),
        FakeResponse(200, {"is_eligible": True, "has_contact_open": False, "web_url": "https://free.fr"}),
        FakeResponse(200, {"banners": [{"title": "Info"}]}),
        FakeResponse(200, {"mail": {"totalItems": 3}, "sms": {"totalItems": 2}}),
        FakeResponse(200, {"retractable": False}),
        FakeResponse(200, {"detailPlanUrl": "https://mobile.free.fr/plan"}),
        FakeResponse(200, [{"iso": "FR", "name": "France"}]),
    ])
    client = FreeMobileApiClient(session, access_token="tok")

    assert asyncio.run(client.async_get_puk(42)) == "12345678"
    assert session.calls[0][2]["params"] == {"offerId": 42}
    assert len(asyncio.run(client.async_get_services("12345678"))) == 2
    assert len(asyncio.run(client.async_get_insurances("12345678"))) == 1
    assert asyncio.run(client.async_get_proxi_eligibility("12345678"))["is_eligible"] is True
    assert len(asyncio.run(client.async_get_banners("12345678"))) == 1
    assert asyncio.run(client.async_get_communications("12345678"))["mail"]["totalItems"] == 3
    assert asyncio.run(client.async_get_retractation("12345678"))["retractable"] is False
    assert asyncio.run(client.async_get_plan_info("12345678"))["detailPlanUrl"] == "https://mobile.free.fr/plan"
    assert asyncio.run(client.async_get_roaming("12345678"))[0]["iso"] == "FR"

    line = FreeMobileLineData(
        login="12345678",
        msisdn="0601020304",
        plan_type="19.99",
        state="active",
        has_freebox_advantage=True,
        is_master=True,
        consumption={"national": {"consumption": {"data": 50 * (1024**3)}}},
        offer={"price": "19,99 €", "nationalData": {"fairuse": 250 * (1024**3), "fairuseFreebox": 300 * (1024**3)}},
        services=[{"id": 1, "name": "VoLTE", "isActive": True}, {"id": 2, "name": "VVM", "isActive": False}],
        sims=[{"simCardType": "eSIM", "state": "active"}],
        communications={"mail": {"totalItems": 3}, "sms": {"totalItems": 2}},
    )
    assert _national_quota_gb(line) == 300.0
    assert _national_remaining_gb(line) == 250.0
    assert _national_usage_pct(line) == 16.7
    assert _offer_price_eur(line) == 19.99
    assert _active_services_count(line) == 1
    assert _sim_type(line) == "eSIM"
    assert _communications_count(line) == 5


def test_coordinator_does_not_poll_puk_until_button_pressed():
    from custom_components.domolink_free_mobile.button import FreeMobileRequestPukButton
    from custom_components.domolink_free_mobile.coordinator import FreeMobileDataUpdateCoordinator
    from custom_components.domolink_free_mobile.sensor import SENSOR_DESCRIPTIONS

    def _line_responses():
        return [
            FakeResponse(200, {
                "debitMethod": {"type": "SEPA"},
                "lines": [{"login": "12345678", "msisdn": "0601020304", "offerId": 42, "state": "active"}],
            }),
            FakeResponse(200, {"national": {"consumption": {"data": 100}}}),
            FakeResponse(200, {"planType": "19.99", "name": "Forfait Free 5G"}),
            FakeResponse(200, {"options": []}),
            FakeResponse(200, {"invoices": []}),
            FakeResponse(200, {"personalUnpaids": []}),
            FakeResponse(200, {"sims": [{"state": "active"}]}),
            FakeResponse(200, {"services": []}),
            FakeResponse(200, {"insurances": []}),
            FakeResponse(200, {}),
            FakeResponse(200, {}),
            FakeResponse(200, {}),
            FakeResponse(200, {"banners": []}),
            FakeResponse(200, {"mail": {"totalItems": 0}}),
        ]

    ha_pn.async_create.reset_mock()
    session = FakeSession(_line_responses())
    client = FreeMobileApiClient(session, access_token="tok")
    coordinator = FreeMobileDataUpdateCoordinator(hass=object(), client=client, main_login="12345678", entry=object())

    data = asyncio.run(coordinator._async_update_data())
    assert all("/puk" not in url for _, url, _ in session.calls)
    assert data["12345678"].puk is None

    coordinator.data = data
    btn = FreeMobileRequestPukButton(coordinator, "12345678")
    btn.hass = object()

    session._responses.append(FakeResponse(200, {"puk": "87654321"}))
    asyncio.run(btn.async_press())

    puk_calls = [(m, u, kw) for m, u, kw in session.calls if "/puk" in u]
    assert len(puk_calls) == 1
    assert puk_calls[0][2]["params"] == {"offerId": 42}
    assert coordinator.data["12345678"].puk == "87654321"

    puk_desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "puk_code")
    assert puk_desc.value_fn(coordinator.data["12345678"]) == "••••21"
    ha_pn.async_create.assert_called_once()
    assert "87654321" in ha_pn.async_create.call_args.kwargs["message"]

    session._responses.extend(_line_responses())
    data2 = asyncio.run(coordinator._async_update_data())
    assert len([(m, u, kw) for m, u, kw in session.calls if "/puk" in u]) == 1
    assert data2["12345678"].puk == "87654321"
