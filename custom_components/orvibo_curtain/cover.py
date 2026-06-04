"""Cover entity for Orvibo curtain motor."""

from __future__ import annotations

import logging

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import OrviboApiError
from .const import CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN
from .coordinator import OrviboCurtainCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the cover entity."""
    coordinator: OrviboCurtainCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OrviboCurtainCover(entry, coordinator)])


class OrviboCurtainCover(CoordinatorEntity[OrviboCurtainCoordinator], CoverEntity):
    """Represents an Orvibo/HomeMate curtain motor."""

    _attr_device_class = CoverDeviceClass.CURTAIN
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, coordinator: OrviboCurtainCoordinator) -> None:
        super().__init__(coordinator)
        self._entry = entry
        device_id = entry.data[CONF_DEVICE_ID]

        self._attr_unique_id = device_id
        self._attr_name = entry.data.get(CONF_DEVICE_NAME) or "Curtain"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=self._attr_name,
            manufacturer="Orvibo",
            model="WiFi Curtain Motor",
            sw_version="5.2.5.301",
        )

    @property
    def current_cover_position(self) -> int | None:
        """Return current position (0=closed, 100=open)."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("position")

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos == 0

    async def async_open_cover(self, **kwargs) -> None:
        """Open the curtain fully."""
        try:
            await self.hass.async_add_executor_job(self.coordinator.api.open_curtain)
        except OrviboApiError as exc:
            _LOGGER.error("Failed to open curtain: %s", exc)
            return
        # Optimistic update
        self.coordinator._last_known_position = 100
        self.coordinator.async_set_updated_data({"position": 100, "is_connected": True})

    async def async_close_cover(self, **kwargs) -> None:
        """Close the curtain fully."""
        try:
            await self.hass.async_add_executor_job(self.coordinator.api.close_curtain)
        except OrviboApiError as exc:
            _LOGGER.error("Failed to close curtain: %s", exc)
            return
        self.coordinator._last_known_position = 0
        self.coordinator.async_set_updated_data({"position": 0, "is_connected": True})

    async def async_stop_cover(self, **kwargs) -> None:
        """Stop the curtain motor."""
        try:
            await self.hass.async_add_executor_job(self.coordinator.api.stop_curtain)
        except OrviboApiError as exc:
            _LOGGER.error("Failed to stop curtain: %s", exc)

    async def async_set_cover_position(self, **kwargs) -> None:
        """Move curtain to a specific position (0-100)."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return
        position = int(position)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.api.set_position, position
            )
        except OrviboApiError as exc:
            _LOGGER.error("Failed to set position %d: %s", position, exc)
            return
        self.coordinator._last_known_position = position
        self.coordinator.async_set_updated_data({"position": position, "is_connected": True})
