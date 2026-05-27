"""Support for AdGuard Home switches."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from adguardhome import AdGuardHome, AdGuardHomeError

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AdGuardConfigEntry, AdGuardData
from .const import DOMAIN
from .entity import AdGuardHomeEntity

SCAN_INTERVAL = timedelta(seconds=10)
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AdGuardHomeSwitchEntityDescription(SwitchEntityDescription):
    """Describes AdGuard Home switch entity."""

    is_on_fn: Callable[[AdGuardHome], Callable[[], Coroutine[Any, Any, bool]]]
    turn_on_fn: Callable[[AdGuardHome], Callable[[], Coroutine[Any, Any, None]]]
    turn_off_fn: Callable[[AdGuardHome], Callable[[], Coroutine[Any, Any, None]]]


# --- Helper methods mapping to the blocked services functionality ---

async def _is_service_blocked(adguard: AdGuardHome, service_name: str) -> bool:
    """Check if a specific service is currently in the blocked list."""
    blocked_services = await adguard.blockedServicesList()
    return service_name in blocked_services


async def _block_service(adguard: AdGuardHome, service_name: str) -> None:
    """Append a service to the blocked list and update via set."""
    blocked_services = await adguard.blockedServicesList()
    if service_name not in blocked_services:
        new_blocked = list(blocked_services)
        new_blocked.append(service_name)
        await adguard.blockedServicesSet(new_blocked)


async def _unblock_service(adguard: AdGuardHome, service_name: str) -> None:
    """Remove a service from the blocked list and update via set."""
    blocked_services = await adguard.blockedServicesList()
    if service_name in blocked_services:
        new_blocked = list(blocked_services)
        new_blocked.remove(service_name)
        await adguard.blockedServicesSet(new_blocked)

# --------------------------------------------------------------------


SWITCHES: tuple[AdGuardHomeSwitchEntityDescription, ...] = (
    AdGuardHomeSwitchEntityDescription(
        key="protection",
        translation_key="protection",
        is_on_fn=lambda adguard: adguard.protection_enabled,
        turn_on_fn=lambda adguard: adguard.enable_protection,
        turn_off_fn=lambda adguard: adguard.disable_protection,
    ),
    AdGuardHomeSwitchEntityDescription(
        key="parental",
        translation_key="parental",
        is_on_fn=lambda adguard: adguard.parental.enabled,
        turn_on_fn=lambda adguard: adguard.parental.enable,
        turn_off_fn=lambda adguard: adguard.parental.disable,
    ),
    AdGuardHomeSwitchEntityDescription(
        key="safesearch",
        translation_key="safe_search",
        is_on_fn=lambda adguard: adguard.safesearch.enabled,
        turn_on_fn=lambda adguard: adguard.safesearch.enable,
        turn_off_fn=lambda adguard: adguard.safesearch.disable,
    ),
    AdGuardHomeSwitchEntityDescription(
        key="safebrowsing",
        translation_key="safe_browsing",
        is_on_fn=lambda adguard: adguard.safebrowsing.enabled,
        turn_on_fn=lambda adguard: adguard.safebrowsing.enable,
        turn_off_fn=lambda adguard: adguard.safebrowsing.disable,
    ),
    AdGuardHomeSwitchEntityDescription(
        key="filtering",
        translation_key="filtering",
        is_on_fn=lambda adguard: adguard.filtering.enabled,
        turn_on_fn=lambda adguard: adguard.filtering.enable,
        turn_off_fn=lambda adguard: adguard.filtering.disable,
    ),
    AdGuardHomeSwitchEntityDescription(
        key="querylog",
        translation_key="query_log",
        is_on_fn=lambda adguard: adguard.querylog.enabled,
        turn_on_fn=lambda adguard: adguard.querylog.enable,
        turn_off_fn=lambda adguard: adguard.querylog.disable,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdGuardConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AdGuard Home switch based on a config entry."""
    data = entry.runtime_data

    # Load static switches
    entities: list[SwitchEntity] = [
        AdGuardHomeSwitch(data, entry, description) for description in SWITCHES
    ]

    # Dynamically fetch available services and generate switches
    try:
        available_services = await data.adguard.blockedServicesAll()
        
        for service in available_services:
            # Ensure compatibility whether the method returns strings or objects
            service_id = service if isinstance(service, str) else service.get("id", str(service))
            service_name = service if isinstance(service, str) else service.get("name", service_id)
            
            # Using default argument binding (s=service_id) inside the lambda 
            # to prevent late-binding issues in Python loops.
            description = AdGuardHomeSwitchEntityDescription(
                key=f"blocked_service_{service_id.lower()}",
                name=f"Block {service_name}",
                icon="mdi:block-helper",
                is_on_fn=lambda adguard, s=service_id: lambda: _is_service_blocked(adguard, s),
                turn_on_fn=lambda adguard, s=service_id: lambda: _block_service(adguard, s),
                turn_off_fn=lambda adguard, s=service_id: lambda: _unblock_service(adguard, s),
            )
            entities.append(AdGuardHomeSwitch(data, entry, description))
            
    except (AdGuardHomeError, AttributeError) as err:
        # Failsafe: if the AdGuard configuration or API client doesn't support the
        # services methods yet, we simply skip creating the dynamic switches.
        pass

    async_add_entities(entities, True)


class AdGuardHomeSwitch(AdGuardHomeEntity, SwitchEntity):
    """Defines a AdGuard Home switch."""

    entity_description: AdGuardHomeSwitchEntityDescription

    def __init__(
        self,
        data: AdGuardData,
        entry: AdGuardConfigEntry,
        description: AdGuardHomeSwitchEntityDescription,
    ) -> None:
        """Initialize AdGuard Home switch."""
        super().__init__(data, entry)
        self.entity_description = description
        
        # Ensures safe assignment for dynamic entities without translation keys
        if hasattr(description, "name") and description.name:
            self._attr_name = description.name

        self._attr_unique_id = "_".join(
            [
                DOMAIN,
                self.adguard.host,
                str(self.adguard.port),
                "switch",
                description.key,
            ]
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        try:
            await self.entity_description.turn_off_fn(self.adguard)()
        except AdGuardHomeError as err:
            self._attr_available = False
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="error_while_turn_off",
            ) from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        try:
            await self.entity_description.turn_on_fn(self.adguard)()
        except AdGuardHomeError as err:
            self._attr_available = False
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="error_while_turn_on",
            ) from err

    async def _adguard_update(self) -> None:
        """Update AdGuard Home entity."""
        self._attr_is_on = await self.entity_description.is_on_fn(self.adguard)()