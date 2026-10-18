"""Cover entity for Orvibo curtain motor."""

from __future__ import annotations

from collections.abc import Callable
import logging

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import OrviboApiError
from .const import (
    CLOSED_THRESHOLD,
    CONF_CLOSED_POSITION,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_UID,
    CONF_INVERT_POSITION,
    CONF_OPEN_POSITION,
    DEFAULT_CLOSED_POSITION,
    DEFAULT_OPEN_POSITION,
    DOMAIN,
)
from .coordinator import OrviboCurtainCoordinator
from .position import to_device_position, to_ha_position

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
        # Entries made before device discovery store the uid as device_id.
        unique_id = entry.data.get(CONF_DEVICE_UID) or entry.data[CONF_DEVICE_ID]
        self._invert = entry.options.get(CONF_INVERT_POSITION, False)
        self._open_position = int(
            entry.options.get(CONF_OPEN_POSITION, DEFAULT_OPEN_POSITION)
        )
        self._closed_position = int(
            entry.options.get(CONF_CLOSED_POSITION, DEFAULT_CLOSED_POSITION)
        )

        self._attr_unique_id = unique_id
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            name=entry.data.get(CONF_DEVICE_NAME) or "Orvibo Curtain",
            manufacturer="Orvibo",
            model="Curtain Motor",
        )

    def _to_ha_position(self, device_pos: int) -> int:
        """Convert device position to HA position (scaled to the open/closed positions)."""
        return to_ha_position(
            device_pos, self._closed_position, self._open_position, self._invert
        )

    def _to_device_position(self, ha_pos: int) -> int:
        """Convert HA position to device position (inverse of _to_ha_position)."""
        return to_device_position(
            ha_pos, self._closed_position, self._open_position, self._invert
        )

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
        return pos <= CLOSED_THRESHOLD

    async def _async_command(self, action: str, func: Callable, *args) -> None:
        try:
            await self.hass.async_add_executor_job(func, *args)
        except OrviboApiError as exc:
            raise HomeAssistantError(f"Failed to {action} curtain: {exc}") from exc

    async def _async_move_to(self, ha_pos: int, action: str) -> None:
        device_pos = self._to_device_position(ha_pos)
        await self._async_command(action, self.coordinator.api.set_position, device_pos)
        self.coordinator.set_position(device_pos)

    async def async_open_cover(self, **kwargs) -> None:
        """Open the curtain to the configured open position (default 100%)."""
        await self._async_move_to(100, "open")

    async def async_close_cover(self, **kwargs) -> None:
        """Close the curtain to the configured closed position (default 0%)."""
        await self._async_move_to(0, "close")

    async def async_stop_cover(self, **kwargs) -> None:
        """Stop the curtain motor."""
        await self._async_command("stop", self.coordinator.api.stop_curtain)

    async def async_set_cover_position(self, **kwargs) -> None:
        """Move curtain to a specific position (0-100)."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return
        await self._async_move_to(int(position), f"move to {int(position)}%")
