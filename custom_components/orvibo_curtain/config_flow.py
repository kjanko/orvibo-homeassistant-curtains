"""Config flow for the Orvibo Curtain integration.

Steps: email + password → family (only if there are several) → pick the curtain
from the account's device list, or enter its uid manually.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .api import (
    CloudAccount,
    OrviboApiError,
    OrviboAuthError,
    OrviboCurtainApi,
    cloud_login,
    fetch_devices,
    is_curtain,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_UID,
    CONF_EMAIL,
    CONF_FAMILY_ID,
    CONF_HOST,
    CONF_CLOSED_POSITION,
    CONF_INVERT_POSITION,
    CONF_OPEN_POSITION,
    CONF_PASSWORD_MD5,
    DEFAULT_CLOSED_POSITION,
    DEFAULT_NAME,
    DEFAULT_OPEN_POSITION,
    DOMAIN,
    ORVIBO_HOSTS,
)

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY = "__manual__"

USER_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
    }
)

MANUAL_SCHEMA = vol.Schema({vol.Required("device_id"): str})


def _md5(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest().upper()


def _device_label(device: dict) -> str:
    name = device.get("deviceName") or device["uid"]
    kind = "curtain" if is_curtain(device) else f"type {device.get('deviceType', '?')}"
    return f"{name} ({kind})"


class OrviboCurtainConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: account login → device picker → curtain entity."""

    VERSION = 1

    def __init__(self) -> None:
        self._email = ""
        self._password_md5 = ""
        self._account: CloudAccount | None = None
        self._family_id = ""
        self._devices: list[dict] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return OrviboCurtainOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input["email"].strip()
            password = user_input["password"].strip()

            if not email or not password:
                errors["base"] = "missing_fields"
            else:
                try:
                    self._account = await self.hass.async_add_executor_job(
                        cloud_login, email, _md5(password), ORVIBO_HOSTS
                    )
                except OrviboAuthError:
                    errors["base"] = "invalid_auth"
                except OrviboApiError as exc:
                    _LOGGER.warning("Login failed: %s", exc)
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during login")
                    errors["base"] = "unknown"
                else:
                    self._email = email
                    self._password_md5 = _md5(password)
                    if len(self._account.families) == 1:
                        self._family_id = self._account.families[0]["familyId"]
                        return await self.async_step_device()
                    return await self.async_step_family()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_family(self, user_input=None):
        """Pick a home when the account has more than one."""
        assert self._account is not None
        if user_input is not None:
            self._family_id = user_input[CONF_FAMILY_ID]
            return await self.async_step_device()

        options = [
            SelectOptionDict(
                value=f["familyId"], label=f.get("familyName") or f["familyId"]
            )
            for f in self._account.families
        ]
        return self.async_show_form(
            step_id="family",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FAMILY_ID): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_device(self, user_input=None):
        """Pick the curtain from the family's device list."""
        assert self._account is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            choice = user_input[CONF_DEVICE_ID]
            if choice == MANUAL_ENTRY:
                return await self.async_step_manual()
            device = next(d for d in self._devices if d["deviceId"] == choice)
            return await self._async_finish(
                device_id=device["deviceId"],
                device_uid=device["uid"],
                name=device.get("deviceName") or DEFAULT_NAME,
            )

        if not self._devices:
            try:
                self._devices = await self.hass.async_add_executor_job(
                    fetch_devices, self._account, self._email, self._family_id
                )
            except OrviboApiError as exc:
                _LOGGER.warning("Could not list devices: %s", exc)
                errors["base"] = "device_list_failed"

        options = [
            SelectOptionDict(value=d["deviceId"], label=_device_label(d))
            for d in self._devices
        ]
        options.append(SelectOptionDict(value=MANUAL_ENTRY, label="Enter device ID manually"))
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_manual(self, user_input=None):
        """Fallback: enter the curtain's uid by hand."""
        if user_input is not None and user_input["device_id"].strip():
            return await self._async_finish(
                device_id=user_input["device_id"].strip(),
                device_uid=None,
                name=DEFAULT_NAME,
                step_id="manual",
            )
        return self.async_show_form(step_id="manual", data_schema=MANUAL_SCHEMA)

    async def _async_finish(
        self,
        device_id: str,
        device_uid: str | None,
        name: str,
        step_id: str = "device",
    ):
        """Check the binary protocol accepts the account, then create the entry."""
        assert self._account is not None
        # Entries made before discovery used the uid as their unique ID.
        await self.async_set_unique_id(device_uid or device_id)
        self._abort_if_unique_id_configured()

        api = OrviboCurtainApi(
            host=self._account.host,
            email=self._email,
            password_md5=self._password_md5,
            family_id=self._family_id,
            device_id=device_id,
            device_uid=device_uid,
        )
        try:
            await self.hass.async_add_executor_job(api.validate_credentials)
        except OrviboApiError as exc:
            _LOGGER.warning("Binary protocol validation failed: %s", exc)
            if step_id == "manual":
                return self.async_show_form(
                    step_id="manual",
                    data_schema=MANUAL_SCHEMA,
                    errors={"base": "cannot_connect"},
                )
            return self.async_abort(reason="cannot_connect")

        data = {
            CONF_EMAIL: self._email,
            CONF_PASSWORD_MD5: self._password_md5,
            CONF_DEVICE_ID: device_id,
            CONF_HOST: self._account.host,
            CONF_FAMILY_ID: self._family_id,
            CONF_DEVICE_NAME: name,
        }
        if device_uid:
            data[CONF_DEVICE_UID] = device_uid
        return self.async_create_entry(title=name, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start re-authentication after the cloud rejects the password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            password_md5 = _md5(user_input["password"].strip())
            try:
                await self.hass.async_add_executor_job(
                    cloud_login, entry.data[CONF_EMAIL], password_md5, ORVIBO_HOSTS
                )
            except OrviboAuthError:
                errors["base"] = "invalid_auth"
            except OrviboApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD_MD5: password_md5}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("password"): str}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )


class OrviboCurtainOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Orvibo Curtain (invert, open and closed position)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_OPEN_POSITION] = int(user_input[CONF_OPEN_POSITION])
            user_input[CONF_CLOSED_POSITION] = int(user_input[CONF_CLOSED_POSITION])
            if user_input[CONF_CLOSED_POSITION] >= user_input[CONF_OPEN_POSITION]:
                errors["base"] = "closed_not_below_open"
            else:
                return self.async_create_entry(title="", data=user_input)

        current = user_input or self._config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_INVERT_POSITION,
                        default=current.get(CONF_INVERT_POSITION, False),
                    ): bool,
                    vol.Optional(
                        CONF_OPEN_POSITION,
                        default=current.get(CONF_OPEN_POSITION, DEFAULT_OPEN_POSITION),
                    ): _percent_slider(1, 100),
                    vol.Optional(
                        CONF_CLOSED_POSITION,
                        default=current.get(CONF_CLOSED_POSITION, DEFAULT_CLOSED_POSITION),
                    ): _percent_slider(0, 50),
                }
            ),
            errors=errors,
        )


def _percent_slider(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="%",
            mode=NumberSelectorMode.SLIDER,
        )
    )
