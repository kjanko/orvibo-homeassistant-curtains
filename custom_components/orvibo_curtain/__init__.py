"""Home Assistant Orvibo Curtain integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .api import OrviboCurtainApi
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_UID,
    CONF_EMAIL,
    CONF_FAMILY_ID,
    CONF_HOST,
    CONF_PASSWORD_MD5,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Orvibo Curtain from a config entry."""
    from .coordinator import OrviboCurtainCoordinator

    data = entry.data
    api = OrviboCurtainApi(
        host=data[CONF_HOST],
        email=data[CONF_EMAIL],
        password_md5=data[CONF_PASSWORD_MD5],
        family_id=data[CONF_FAMILY_ID],
        device_id=data[CONF_DEVICE_ID],
        device_uid=data.get(CONF_DEVICE_UID),
    )

    coordinator = OrviboCurtainCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Orvibo Curtain config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
