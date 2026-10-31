"""Constants for the GOWA API integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "gowa"
PLATFORMS = [Platform.BINARY_SENSOR, Platform.NOTIFY, Platform.SENSOR]
UPDATE_INTERVAL = timedelta(seconds=60)
CONF_DEVICE_ID = "device_id"
CONF_RECIPIENTS = "recipients"
CONF_DEFAULT_COUNTRY = "default_country"
CONF_PHONE = "phone"
CONF_PERSON = "person"
SERVICE_SEND_MESSAGE = "send_message"
ISSUE_CONNECTION = "connection_problem"
