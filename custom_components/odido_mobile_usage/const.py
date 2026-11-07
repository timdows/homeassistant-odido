"""Constants for the Odido Mobile Usage integration."""

from datetime import timedelta

DOMAIN = "odido_mobile_usage"
PLATFORMS = ["sensor"]

LOGIN_URL = "https://www.odido.nl/login"

UPDATE_INTERVAL = timedelta(minutes=30)
