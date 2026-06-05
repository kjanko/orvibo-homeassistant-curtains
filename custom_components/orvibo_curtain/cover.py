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
from .const import CONF_DEVICE_ID, CONF_DEVICE_NAME, CONF_INVERT_POSITION, DOMAIN
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
        self._invert = entry.options.get(CONF_INVERT_POSITION, False)

        self._attr_unique_id = device_id
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.data.get(CONF_DEVICE_NAME) or "Orvibo Curtain",
            manufacturer="Orvibo",
            model="WiFi Curtain Motor",
            sw_version="5.2.5.301",
        )

    def _to_ha_position(self, device_pos: int) -> int:
        """Convert device position to HA position (respecting invert)."""
        return 100 - device_pos if self._invert else device_pos

    def _to_device_position(self, ha_pos: int) -> int:
        """Convert HA position to device position (respecting invert)."""
        return 100 - ha_pos if self._invert else ha_pos

    @property
    def current_cover_position(self) -> int | None:
        """Return current position (0=closed, 100=open)."""
        if self.coordinator.data is None:
            return None
        pos = self.coordinator.data.get("position")
        if pos is None:
            return None
        return self._to_ha_position(pos)

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos == 0

    async def async_open_cover(self, **kwargs) -> None:
        """Open the curtain fully."""
        try:
            if self._invert:
                await self.hass.async_add_executor_job(self.coordinator.api.close_curtain)
            else:
                await self.hass.async_add_executor_job(self.coordinator.api.open_curtain)
        except OrviboApiError as exc:
            _LOGGER.error("Failed to open curtain: %s", exc)
            return
        device_pos = 0 if self._invert else 100
        self.coordinator._last_known_position = device_pos
        self.coordinator.async_set_updated_data({"position": device_pos, "is_connected": True})

    async def async_close_cover(self, **kwargs) -> None:
        """Close the curtain fully."""
        try:
            if self._invert:
                await self.hass.async_add_executor_job(self.coordinator.api.open_curtain)
            else:
                await self.hass.async_add_executor_job(self.coordinator.api.close_curtain)
        except OrviboApiError as exc:
            _LOGGER.error("Failed to close curtain: %s", exc)
            return
        device_pos = 100 if self._invert else 0
        self.coordinator._last_known_position = device_pos
        self.coordinator.async_set_updated_data({"position": device_pos, "is_connected": True})

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
        device_pos = self._to_device_position(position)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.api.set_position, device_pos
            )
        except OrviboApiError as exc:
            _LOGGER.error("Failed to set position %d: %s", position, exc)
            return
        self.coordinator._last_known_position = device_pos
        self.coordinator.async_set_updated_data({"position": device_pos, "is_connected": True})
