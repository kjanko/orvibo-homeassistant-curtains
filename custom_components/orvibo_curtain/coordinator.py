"""Data update coordinator for Orvibo curtain state."""

from __future__ import annotations

from datetime import timedelta
import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OrviboApiError, OrviboAuthError, OrviboCurtainApi
from .const import AUTH_FAILURES_BEFORE_REAUTH, CLOUD_POSITION_INTERVAL, DEFAULT_POLL_INTERVAL

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
        self._auth_failures = 0
        self._last_cloud_check: float | None = None

    async def _async_update_data(self) -> dict:
        """Poll the curtain. Position may be None if the curtain is idle."""
        try:
            position = await self.hass.async_add_executor_job(self.api.get_position)
        except OrviboAuthError as exc:
            # A single rejection can be a server hiccup; repeated ones mean
            # the password changed, so ask the user to re-authenticate.
            self._auth_failures += 1
            if self._auth_failures >= AUTH_FAILURES_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed(str(exc)) from exc
            raise UpdateFailed(str(exc)) from exc
        except OrviboApiError as exc:
            # Connection/login failed — entity should show as unavailable
            raise UpdateFailed(str(exc)) from exc
        except Exception as exc:
            raise UpdateFailed(f"Unexpected error: {exc}") from exc

        self._auth_failures = 0
        if position is None and self._cloud_check_due():
            position = await self._async_cloud_position()
        # If position came back, update our cache
        if position is not None:
            self._last_known_position = position

        return {
            "position": self._last_known_position,
            "is_connected": True,
        }

    def _cloud_check_due(self) -> bool:
        """Ask the cloud when the position is unknown, else at most every 10 minutes."""
        if self._last_known_position is None or self._last_cloud_check is None:
            return True
        return time.monotonic() - self._last_cloud_check >= CLOUD_POSITION_INTERVAL

    async def _async_cloud_position(self) -> int | None:
        """The cloud's recorded position; None (and a debug log) if unavailable."""
        self._last_cloud_check = time.monotonic()
        try:
            return await self.hass.async_add_executor_job(self.api.get_cloud_position)
        except Exception as exc:  # the binary session works, so don't fail the poll
            _LOGGER.debug("Cloud position lookup failed: %s", exc)
            return None

    def set_position(self, device_pos: int) -> None:
        """Record a position after a successful command."""
        self._last_known_position = device_pos
        self.async_set_updated_data({"position": device_pos, "is_connected": True})

    def restore_position(self, device_pos: int) -> None:
        """Seed the position from before a restart; a real poll result wins."""
        if self._last_known_position is not None:
            return
        self._last_known_position = device_pos
        self.data = {**(self.data or {}), "position": device_pos}
