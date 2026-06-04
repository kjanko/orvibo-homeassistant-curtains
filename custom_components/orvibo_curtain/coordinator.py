"""Data update coordinator for Orvibo curtain state."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OrviboApiError, OrviboCurtainApi
from .const import DEFAULT_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class OrviboCurtainCoordinator(DataUpdateCoordinator[dict]):
    """Polls the curtain position periodically.

    Data dict:
      - position: int | None  (0-100, or None if curtain didn't report)
      - is_connected: bool     (True if last poll reached the server)
    """

    def __init__(self, hass: HomeAssistant, api: OrviboCurtainApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Orvibo Curtain",
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL),
        )
        self.api = api
        # Track last known position across polls (curtain may not always reply)
        self._last_known_position: int | None = None

    async def _async_update_data(self) -> dict:
        """Poll the curtain. Position may be None if the curtain is idle."""
        try:
            position = await self.hass.async_add_executor_job(self.api.get_position)
        except OrviboApiError as exc:
            # Connection/login failed — entity should show as unavailable
            raise UpdateFailed(str(exc)) from exc
        except Exception as exc:
            raise UpdateFailed(f"Unexpected error: {exc}") from exc

        # If position came back, update our cache
        if position is not None:
            self._last_known_position = position

        return {
            "position": self._last_known_position,
            "is_connected": True,
        }
