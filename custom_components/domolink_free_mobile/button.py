"""Button platform for DomoLink Free Mobile."""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import FreeMobileApiError, FreeMobileAuthError
from .const import DOMAIN, MANUFACTURER
from .coordinator import FreeMobileDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Free Mobile button entities from a config entry."""
    coordinator: FreeMobileDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        FreeMobileRequestPukButton(coordinator, login)
        for login in coordinator.data
    ]
    async_add_entities(entities)


class FreeMobileRequestPukButton(
    CoordinatorEntity[FreeMobileDataUpdateCoordinator], ButtonEntity
):
    """Diagnostic button to explicitly fetch the PUK code on demand."""

    _attr_has_entity_name = True
    _attr_name = "Récupérer le code PUK"
    _attr_icon = "mdi:sim-alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: FreeMobileDataUpdateCoordinator,
        login: str,
    ) -> None:
        super().__init__(coordinator)
        self._login = login
        self._attr_unique_id = f"{login}_request_puk"
        line = coordinator.data[login]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, login)},
            name=f"Free Mobile {line.msisdn}",
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self) -> bool:
        """Return True if coordinator is available and line has an offer_id."""
        line = self.coordinator.data.get(self._login) if self.coordinator.data else None
        return super().available and bool(line and line.offer_id is not None)

    async def async_press(self) -> None:
        """Fetch PUK code once on manual press and display a notification."""
        try:
            puk = await self.coordinator.async_request_puk(self._login)
        except FreeMobileAuthError as err:
            if hasattr(self.coordinator.entry, "async_start_reauth"):
                self.coordinator.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"La session Free Mobile a expiré : {err}") from err
        except FreeMobileApiError as err:
            raise HomeAssistantError(f"Impossible de récupérer le code PUK : {err}") from err

        msisdn = (
            self.coordinator.data[self._login].msisdn
            if self.coordinator.data and self._login in self.coordinator.data
            else self._login
        )
        persistent_notification.async_create(
            self.hass,
            message=(
                f"Code PUK de la ligne **{msisdn}** : `{puk}`\n\n"
                "*(Note : Free Mobile envoie automatiquement un e-mail de sécurité à chaque "
                "consultation du code PUK. Vous pouvez fermer cette notification une fois le code utilisé.)*"
            ),
            title=f"Code PUK Free Mobile ({msisdn})",
            notification_id=f"{DOMAIN}_puk_{self._login}",
        )
