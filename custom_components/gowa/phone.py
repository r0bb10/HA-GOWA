"""Explicit international phone validation; no country-code heuristics."""

import re

import phonenumbers


def normalize_country(value: str) -> str:
    """An empty country requires international input; otherwise use ISO alpha-2."""
    country = value.strip().upper()
    if country and country not in phonenumbers.SUPPORTED_REGIONS:
        raise ValueError("Choose a valid two-letter country code.")
    return country


def normalize_phone(value: str, country: str = "") -> str:
    """Return an E.164 numeric string, suitable for the tested GOWA endpoint."""
    if not isinstance(value, str) or not re.fullmatch(
        r"\+?[0-9 ()\-.]+", value.strip()
    ):
        raise ValueError("Enter a valid phone number, not a WhatsApp JID.")
    country = normalize_country(country)
    raw = re.sub(r"[ ()\-.]", "", value.strip())
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    candidates = set()
    # Bare full international numbers are the existing GOWA convention.
    inputs = [(raw if raw.startswith("+") else "+" + raw, None)]
    if not raw.startswith("+") and country:
        inputs.append((raw, country))
    for number, region in inputs:
        try:
            parsed = phonenumbers.parse(number, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            candidates.add(
                phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)[
                    1:
                ]
            )
    if len(candidates) == 1:
        return candidates.pop()
    raise ValueError(
        "Enter a valid international number or configure its default country."
    )
