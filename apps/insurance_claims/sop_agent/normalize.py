"""
Convert names, phones, emails, dates, and ID digits into one standard form, so "==" compares them.
"""

import re
from datetime import datetime

# Accepted as a second check. The extraction LLM already gives YYYY-MM-DD.
_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y", "%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y"]


def normalize_name(value: str) -> str:
    """"Chen, Margaret" -> "chen margaret". Sorted words, so the name order does not matter."""
    words = re.sub(r"[^\w\s]", " ", value.lower()).split()
    return " ".join(sorted(words))


def normalize_phone(value: str) -> str:
    """"(650) 521-2836" -> "16505212836". Adds the US country code to 10-digit numbers."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = "1" + digits
    return digits


def normalize_email(value: str) -> str:
    """" Margaret@Email.com " -> "margaret@email.com"."""
    return re.sub(r"\s", "", value.lower())


def normalize_dob(value: str) -> str:
    """Return "YYYY-MM-DD", or the value unchanged if it cannot be parsed (then it does not match)."""
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def normalize_last4(value: str) -> str:
    """"xxx-xx-4472" -> "4472"."""
    digits = re.sub(r"\D", "", value)
    return digits[-4:]
