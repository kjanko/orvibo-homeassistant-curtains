"""Config flow for the Orvibo Curtain integration.

User inputs: email, password, device_id. That's it.
"""

from __future__ import annotations

import hashlib
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .api import OrviboApiError, OrviboCurtainApi, discover_family
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_EMAIL,
    CONF_FAMILY_ID,
    CONF_HOST,
    CONF_PASSWORD_MD5,
    DEFAULT_NAME,
    DOMAIN,
    ORVIBO_HOSTS,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
        vol.Required("device_id"): str,
    }
)


class OrviboCurtainConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: email + password + device_id → curtain entity."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input["email"].strip()
            password = user_input["password"].strip()
            device_id = user_input["device_id"].strip()

            if not email or not password or not device_id:
                errors["base"] = "invalid_auth"
            else:
                password_md5 = hashlib.md5(password.encode()).hexdigest().upper()

                # Step 1: Discover host + familyId from REST API
                try:
                    host, family_id = await self.hass.async_add_executor_job(
                        discover_family, email, password_md5, ORVIBO_HOSTS
                    )
                except OrviboApiError as exc:
                    _LOGGER.warning("Discovery failed: %s", exc)
                    errors["base"] = "cannot_connect"
                except Exception as exc:
                    _LOGGER.exception("Unexpected error: %s", exc)
                    errors["base"] = "unknown"
                else:
                    # Step 2: Validate binary protocol works
                    api = OrviboCurtainApi(
                        host=host,
                        email=email,
                        password_md5=password_md5,
                        family_id=family_id,
                        device_id=device_id,
                    )
                    try:
                        await self.hass.async_add_executor_job(api.validate_credentials)
                    except OrviboApiError as exc:
                        _LOGGER.warning("Binary protocol validation failed: %s", exc)
                        errors["base"] = "cannot_connect"
                    except Exception as exc:
                        _LOGGER.exception("Unexpected error: %s", exc)
                        errors["base"] = "unknown"
                    else:
                        await self.async_set_unique_id(device_id)
                        self._abort_if_unique_id_configured()

                        return self.async_create_entry(
                            title=DEFAULT_NAME,
                            data={
                                CONF_EMAIL: email,
                                CONF_PASSWORD_MD5: password_md5,
                                CONF_DEVICE_ID: device_id,
                                CONF_HOST: host,
                                CONF_FAMILY_ID: family_id,
                                CONF_DEVICE_NAME: DEFAULT_NAME,
                            },
                        )

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )
