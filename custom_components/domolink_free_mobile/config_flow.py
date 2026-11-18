"""Config flow for DomoLink Free Mobile."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    FreeMobileApiClient,
    FreeMobileApiError,
    FreeMobileAuthError,
    FreeMobileOtpRequired,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_OTP_CODE,
    CONF_REFRESH_TOKEN,
    CONF_SWITCH_EMAIL_OTP,
    CONF_TRUST_UUID,
    DOMAIN,
)


class FreeMobileConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle Free Mobile credential and 2FA OTP configuration flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._otp_id: int | None = None
        self._type_2fa: str = "sms"
        self._trust_uuid: str | None = None
        self._temp_access_token: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip()
            password = str(user_input[CONF_PASSWORD])
            self._username = username

            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()

            client = FreeMobileApiClient(async_get_clientsession(self.hass))
            try:
                data = await client.async_login(username, password)
                return self._create_token_entry(username, data, client.trust_uuid)
            except FreeMobileOtpRequired as otp_req:
                self._otp_id = otp_req.otp_id
                self._type_2fa = otp_req.type_2fa
                self._trust_uuid = otp_req.trust_uuid
                self._temp_access_token = otp_req.access_token
                return await self.async_step_otp()
            except FreeMobileAuthError:
                errors["base"] = "invalid_auth"
            except FreeMobileApiError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = FreeMobileApiClient(
                async_get_clientsession(self.hass),
                access_token=self._temp_access_token,
                trust_uuid=self._trust_uuid,
            )
            if user_input.get(CONF_SWITCH_EMAIL_OTP):
                try:
                    switched = await client.async_switch_otp_email(self._username)
                    if switched.get("id") is not None:
                        self._otp_id = int(switched["id"])
                    self._type_2fa = str(switched.get("transport", "email"))
                except FreeMobileApiError as err:
                    errors["base"] = "server_error" if "HTTP 5" in str(err) else "cannot_connect"
            else:
                raw_code = str(user_input.get(CONF_OTP_CODE, "")).strip()
                if not raw_code.isdigit() or self._otp_id is None:
                    errors["base"] = "invalid_otp"
                else:
                    try:
                        data = await client.async_validate_otp(
                            otp_id=self._otp_id,
                            code_otp=int(raw_code),
                            is_trusted=True,
                        )
                        return self._create_token_entry(self._username, data, client.trust_uuid)
                    except FreeMobileAuthError:
                        errors["base"] = "invalid_otp"
                    except FreeMobileApiError as err:
                        errors["base"] = "server_error" if "HTTP 5" in str(err) else "cannot_connect"

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_OTP_CODE, default=""): str,
                    vol.Optional(CONF_SWITCH_EMAIL_OTP, default=False): bool,
                }
            ),
            description_placeholders={"type_2fa": self._type_2fa},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._username = str(entry_data.get(CONF_USERNAME, ""))
        self._trust_uuid = entry_data.get(CONF_TRUST_UUID)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = FreeMobileApiClient(
                async_get_clientsession(self.hass),
                access_token=self._temp_access_token,
                trust_uuid=self._trust_uuid,
            )
            try:
                data = await client.async_login(self._username, str(user_input[CONF_PASSWORD]))
                return self._update_reauth_entry(data, client.trust_uuid)
            except FreeMobileOtpRequired as otp_req:
                self._otp_id = otp_req.otp_id
                self._type_2fa = otp_req.type_2fa
                self._trust_uuid = otp_req.trust_uuid
                self._temp_access_token = otp_req.access_token
                return await self.async_step_otp()
            except FreeMobileAuthError:
                errors["base"] = "invalid_auth"
            except FreeMobileApiError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": self._username},
            errors=errors,
        )

    def _create_token_entry(
        self,
        username: str,
        token_data: dict[str, Any],
        trust_uuid: str | None,
    ) -> ConfigFlowResult:
        entry_data = {
            CONF_USERNAME: username,
            CONF_ACCESS_TOKEN: token_data["accessToken"],
            CONF_REFRESH_TOKEN: token_data.get("refreshToken", ""),
            CONF_TRUST_UUID: trust_uuid or token_data.get("trustUuid") or self._trust_uuid,
        }
        if getattr(self, "source", None) == SOURCE_REAUTH:
            if hasattr(self, "_get_reauth_entry"):
                reauth_entry = self._get_reauth_entry()
            else:
                entry_id = getattr(self, "context", {}).get("entry_id")
                reauth_entry = (
                    self.hass.config_entries.async_get_entry(entry_id)
                    if entry_id and getattr(self, "hass", None) is not None
                    else None
                )
            if reauth_entry is not None:
                return self.async_update_reload_and_abort(reauth_entry, data=entry_data)
        return self.async_create_entry(title=f"Free Mobile ({username})", data=entry_data)

    def _update_reauth_entry(
        self, token_data: dict[str, Any], trust_uuid: str | None
    ) -> ConfigFlowResult:
        return self._create_token_entry(self._username, token_data, trust_uuid)
