"""API client for Free Mobile (reverse-engineered from fr.free.moncomptefree)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

from .const import (
    BASE_URL,
    HEADER_FREE_APP_PLATFORM,
    HEADER_FREE_APP_PLATFORM_VALUE,
    HEADER_FREE_VERSION,
    HEADER_FREE_VERSION_VALUE,
    HEADER_PLATFORM,
    HEADER_PLATFORM_VALUE,
)


class FreeMobileApiError(Exception):
    """Generic Free Mobile API error."""


class FreeMobileAuthError(FreeMobileApiError):
    """Authentication or authorization failure."""


class FreeMobileOtpRequired(FreeMobileApiError):
    """Raised when Free Mobile 2FA OTP validation is required."""

    def __init__(
        self,
        otp_id: int,
        type_2fa: str,
        trust_uuid: str | None = None,
        access_token: str | None = None,
    ) -> None:
        super().__init__(f"2FA OTP required ({type_2fa}, id={otp_id})")
        self.otp_id = otp_id
        self.type_2fa = type_2fa
        self.trust_uuid = trust_uuid
        self.access_token = access_token


class FreeMobileApiClient:
    """Async REST client for https://mobile.free.fr/account/api/."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str | None = None,
        refresh_token: str | None = None,
        trust_uuid: str | None = None,
        on_tokens_refreshed: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._session = session
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.trust_uuid = trust_uuid
        self._on_tokens_refreshed = on_tokens_refreshed
        self._refresh_lock = asyncio.Lock()

    def _base_headers(
        self,
        *,
        include_auth: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {
            HEADER_PLATFORM: HEADER_PLATFORM_VALUE,
            HEADER_FREE_APP_PLATFORM: HEADER_FREE_APP_PLATFORM_VALUE,
            "X-App-Platform": "android",
            "X-App-Version": "3.9.1",
            "User-Agent": "Free/3.9.1 (Android)",
            "Accept": "application/json",
        }
        if include_auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        include_auth: bool = True,
        retry_on_401: bool = True,
        raw_bytes: bool = False,
    ) -> Any:
        url = f"{BASE_URL}{path.lstrip('/')}"
        headers = self._base_headers(include_auth=include_auth, extra_headers=extra_headers)

        try:
            async with self._session.request(
                method,
                url,
                json=json_body,
                data=form_data,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 401 and retry_on_401 and include_auth and self.refresh_token:
                    await self.async_refresh_token()
                    return await self._request(
                        method,
                        path,
                        json_body=json_body,
                        form_data=form_data,
                        params=params,
                        extra_headers=extra_headers,
                        include_auth=include_auth,
                        retry_on_401=False,
                        raw_bytes=raw_bytes,
                    )
                if resp.status in (401, 403):
                    text = await resp.text()
                    _LOGGER.warning("Free Mobile auth error on %s (HTTP %s): %s", path, resp.status, text)
                    raise FreeMobileAuthError(f"HTTP {resp.status}: {text}")
                if resp.status >= 400:
                    text = await resp.text()
                    _LOGGER.warning("Free Mobile API error on %s (HTTP %s): %s", path, resp.status, text)
                    raise FreeMobileApiError(f"HTTP {resp.status} on {path}: {text}")
                if raw_bytes:
                    return await resp.read()
                if resp.content_type == "application/json":
                    return await resp.json()
                text = await resp.text()
                return {"raw": text} if text else {}
        except aiohttp.ClientError as err:
            raise FreeMobileApiError(f"Network error calling {path}: {err}") from err

    async def async_login(self, username: str, password: str) -> dict[str, Any]:
        """Authenticate with Free Mobile credentials; raises FreeMobileOtpRequired on 2FA."""
        payload: dict[str, Any] = {"username": username, "password": password}
        if self.trust_uuid:
            payload["trustedUuid"] = self.trust_uuid

        data = await self._request(
            "POST",
            "/account/api/auth/login",
            json_body=payload,
            include_auth=False,
            retry_on_401=False,
        )
        otp_id = data.get("otpId")
        if otp_id is not None:
            temp_token = data.get("accessToken")
            if temp_token:
                self.access_token = temp_token
            if data.get("trustUuid"):
                self.trust_uuid = data["trustUuid"]
            raise FreeMobileOtpRequired(
                otp_id=int(otp_id),
                type_2fa=str(data.get("type2FA", "sms")),
                trust_uuid=data.get("trustUuid"),
                access_token=temp_token,
            )

        self.access_token = data.get("accessToken")
        self.refresh_token = data.get("refreshToken")
        if data.get("trustUuid"):
            self.trust_uuid = data["trustUuid"]
        if not self.access_token:
            raise FreeMobileAuthError("Missing accessToken in login response")
        return data

    async def async_validate_otp(
        self,
        otp_id: int,
        code_otp: int,
        is_trusted: bool = True,
    ) -> dict[str, Any]:
        """Validate 2FA OTP code and store resulting bearer tokens."""
        data = await self._request(
            "POST",
            "/account/api/auth/otp",
            json_body={"idOtp": otp_id, "codeOtp": code_otp, "isTrusted": is_trusted},
            include_auth=bool(self.access_token),
            retry_on_401=False,
        )
        self.access_token = data.get("accessToken")
        self.refresh_token = data.get("refreshToken")
        if data.get("trustUuid"):
            self.trust_uuid = data["trustUuid"]
        if not self.access_token:
            raise FreeMobileAuthError("Missing accessToken in OTP validation response")
        return data

    async def async_switch_otp_email(self, login: str) -> dict[str, Any]:
        """Request delivery of the 2FA OTP code via email instead of SMS."""
        return await self._request(
            "POST",
            "/account/api/auth/otp/mail",
            json_body={"login": login},
            include_auth=bool(self.access_token),
            retry_on_401=False,
        )

    async def async_refresh_token(self) -> dict[str, Any]:
        """Refresh the bearer access token safely under an async lock."""
        if not self.refresh_token:
            raise FreeMobileAuthError("No refresh_token available")

        previous_access = self.access_token
        async with self._refresh_lock:
            # Another coroutine may have already refreshed while waiting on the lock.
            if self.access_token != previous_access and self.access_token:
                return {
                    "accessToken": self.access_token,
                    "refreshToken": self.refresh_token,
                }
            data = await self._request(
                "POST",
                "account/api/auth/refreshToken",
                form_data={"refreshToken": self.refresh_token},
                include_auth=False,
                retry_on_401=False,
            )
            new_access = data.get("accessToken")
            new_refresh = data.get("refreshToken") or self.refresh_token
            if not new_access:
                raise FreeMobileAuthError("Failed to refresh Free Mobile accessToken")
            self.access_token = new_access
            self.refresh_token = new_refresh
            if self._on_tokens_refreshed:
                res = self._on_tokens_refreshed(new_access, new_refresh)
                if asyncio.iscoroutine(res):
                    await res
            return data

    async def async_get_subscriber(self, login: str) -> dict[str, Any]:
        """Fetch main subscriber profile and associated lines."""
        return await self._request("GET", f"account/api/subscriber/{login}")

    async def async_get_consumption(self, login: str) -> dict[str, Any]:
        """Fetch national and roaming consumption & out-of-bundle billing."""
        return await self._request("GET", f"account/api/subscriber/{login}/consumption")

    async def async_get_offer(self, login: str) -> dict[str, Any]:
        """Fetch current plan/offer details, fair-use quotas, and booster."""
        return await self._request("GET", f"account/api/subscriber/{login}/offer")

    async def async_get_options(self, login: str) -> list[dict[str, Any]]:
        """Fetch mobile line options using X-Free-Version: 2."""
        res = await self._request(
            "GET",
            f"/account/api/subscriber/{login}/options",
            extra_headers={HEADER_FREE_VERSION: HEADER_FREE_VERSION_VALUE},
        )
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            return res.get("options", [])
        return []

    async def async_set_option(
        self,
        login: str,
        option_id: str,
        enable: bool,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Activate (POST) or deactivate (PUT) a Free Mobile line option."""
        method = "POST" if enable else "PUT"
        path = (
            f"/account/api/subscriber/{login}/options/activate"
            if enable
            else f"/account/api/subscriber/{login}/options/desactivate"
        )
        payload: dict[str, Any] = {"option": option_id}
        if extra:
            payload["extra"] = extra
        res = await self._request(method, path, json_body=payload)
        return bool(res.get("success", True)) if isinstance(res, dict) else True

    async def async_get_invoices(self, login: str) -> dict[str, Any]:
        """Fetch billing invoices and summaries."""
        return await self._request("GET", f"account/api/subscriber/{login}/document/invoices")

    async def async_get_unpaids(self, login: str) -> dict[str, Any]:
        """Fetch unpaid bills summary."""
        return await self._request("GET", f"account/api/subscriber/{login}/unpaids")

    async def async_get_sims(self, login: str) -> list[dict[str, Any]]:
        """Fetch SIM / eSIM cards attached to the line."""
        res = await self._request("GET", f"/account/api/subscriber/{login}/sims")
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            return res.get("sims", [])
        return []

    async def async_get_puk(self, offer_id: int) -> str | None:
        """Fetch the PUK code for a given offerId."""
        res = await self._request("GET", "/account/api/puk", params={"offerId": offer_id})
        if isinstance(res, dict):
            return res.get("puk")
        return None

    async def async_get_services(self, login: str) -> list[dict[str, Any]]:
        """Fetch active/inactive services for the line."""
        res = await self._request("GET", f"/account/api/subscriber/{login}/services")
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            return res.get("services", [])
        return []

    async def async_get_insurances(self, login: str) -> list[dict[str, Any]]:
        """Fetch mobile insurances attached to the line."""
        res = await self._request("GET", f"/account/api/subscriber/{login}/insurances")
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            return res.get("insurances", [])
        return []

    async def async_get_plan_info(self, login: str) -> dict[str, Any]:
        """Fetch detailed plan info (URLs and roaming zones)."""
        res = await self._request("GET", f"/account/api/subscriber/{login}/plan-infos")
        return res if isinstance(res, dict) else {}

    async def async_get_proxi_eligibility(self, login: str) -> dict[str, Any]:
        """Fetch Free Proxi support eligibility and open contact status."""
        res = await self._request("GET", f"/account/api/subscriber/{login}/proxi/eligibility")
        return res if isinstance(res, dict) else {}

    async def async_get_retractation(self, login: str) -> dict[str, Any]:
        """Fetch retractation window status for the line."""
        res = await self._request("GET", f"/account/api/subscriber/{login}/retractable")
        return res if isinstance(res, dict) else {}

    async def async_get_banners(self, login: str) -> list[dict[str, Any]]:
        """Fetch account banners/alerts for the subscriber."""
        res = await self._request(
            "GET",
            f"account/api/banners/{login}",
            extra_headers={"Content-Type": "application/json", HEADER_PLATFORM: HEADER_PLATFORM_VALUE},
        )
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            return res.get("banners", [])
        return []

    async def async_get_communications(
        self,
        login: str,
        comm_type: str | None = None,
        per_page: int = 10,
        page: int = 1,
    ) -> dict[str, Any]:
        """Fetch recent SMS and email communications sent by Free Mobile."""
        params: dict[str, Any] = {"perPage": per_page, "page": page}
        if comm_type:
            params["type"] = comm_type
        res = await self._request(
            "GET",
            f"/account/api/subscriber/{login}/communications",
            params=params,
        )
        return res if isinstance(res, dict) else {}

    async def async_get_roaming(
        self,
        login: str,
        mcc: str | None = None,
        iso: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Fetch roaming rates for all countries or a specific mcc/iso."""
        params: dict[str, Any] = {}
        if mcc:
            params["mcc"] = mcc
        if iso:
            params["iso"] = iso
        return await self._request(
            "GET",
            f"/account/api/roaming/{login}",
            params=params or None,
        )

    async def async_get_invoice_pdf(self, login: str, invoice_id: str | int) -> bytes:
        """Download an invoice PDF as raw bytes."""
        return await self._request(
            "GET",
            f"account/api/subscriber/{login}/document/invoices/{invoice_id}",
            raw_bytes=True,
        )

    async def async_activate_sim(
        self,
        login: str,
        iccid: str,
        offer_id: int,
        force_retraction: bool = False,
    ) -> dict[str, Any]:
        """Activate a physical SIM card by ICCID."""
        res = await self._request(
            "POST",
            f"/account/api/subscriber/{login}/sims/{iccid}/activate",
            json_body={"offer": offer_id, "forceRetraction": force_retraction},
        )
        return res if isinstance(res, dict) else {}

    async def async_activate_esim(self, login: str, force: bool = False) -> dict[str, Any]:
        """Activate an eSIM on the line."""
        res = await self._request(
            "POST",
            f"/account/api/subscriber/{login}/esim/activate",
            json_body={"force": force},
        )
        return res if isinstance(res, dict) else {}

    async def async_change_email(self, login: str, email: str) -> dict[str, Any]:
        """Update the contact email address on the subscriber line."""
        res = await self._request(
            "POST",
            f"account/api/subscriber/{login}/change-email",
            json_body={"email": email},
        )
        return res if isinstance(res, dict) else {}
