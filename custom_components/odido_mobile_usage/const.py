"""Constants for the Odido Mobile Usage integration."""

from datetime import timedelta

DOMAIN = "odido_mobile_usage"
PLATFORMS = ["sensor"]

CONF_COOKIES = "cookies"
CONF_USAGE_URL = "usage_url"
DEFAULT_USAGE_URL = "https://www.odido.nl/my/tegoed"
LOGIN_URL = "https://www.odido.nl/login"

UPDATE_INTERVAL = timedelta(minutes=30)
