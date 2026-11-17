"""Constants for the DomoLink Free Mobile integration."""

from datetime import timedelta

DOMAIN = "domolink_free_mobile"
MANUFACTURER = "Free Mobile"

BASE_URL = "https://mobile.free.fr/"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=15)

CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TRUST_UUID = "trust_uuid"
CONF_OTP_CODE = "otp_code"
CONF_SWITCH_EMAIL_OTP = "switch_email_otp"

HEADER_PLATFORM = "x-platform"
HEADER_PLATFORM_VALUE = "android"
HEADER_FREE_VERSION = "X-Free-Version"
HEADER_FREE_VERSION_VALUE = "2"

CONTROLLABLE_OPTION_IDS: tuple[str, ...] = (
    "_5g",
    "_5gsa",
    "international",
    "voyage",
    "blockPaidNumber",
    "data",
    "forfaitBlocked",
    "smsNotifications",
    "nvpn",
)

OPTION_LABELS: dict[str, str] = {
    "_5g": "Option 5G",
    "_5gsa": "Option 5G SA",
    "international": "Appels / Data International",
    "voyage": "Option Voyage",
    "blockPaidNumber": "Blocage numéros surtaxés",
    "data": "Données mobiles (Data)",
    "forfaitBlocked": "Forfait bloqué",
    "smsNotifications": "Notifications SMS",
    "nvpn": "mVPN",
}
