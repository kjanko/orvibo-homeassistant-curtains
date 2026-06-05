"""Constants for the Orvibo Curtain integration."""

DOMAIN = "orvibo_curtain"
PLATFORMS = ["cover"]

DEFAULT_PORT = 10002
DEFAULT_NAME = "Orvibo Curtain"
DEFAULT_POLL_INTERVAL = 60

# Bundled cert/key filenames
BUNDLED_CERT = "orvibo_client_cert.pem"
BUNDLED_KEY = "orvibo_client_key.pem"

# Regional servers to try during discovery
ORVIBO_HOSTS = [
    "germany.orvibo.com",
    "homemate.orvibo.com",
    "usa.orvibo.com",
    "china.orvibo.com",
]

# Config entry data keys
CONF_EMAIL = "email"
CONF_PASSWORD_MD5 = "password_md5"
CONF_DEVICE_ID = "device_id"
CONF_HOST = "host"
CONF_FAMILY_ID = "family_id"
CONF_DEVICE_NAME = "device_name"
CONF_INVERT_POSITION = "invert_position"
