"""
Bilingual date parsing for Wallapop deadline strings.

Wallapop shipping deadlines come as localized date strings in Spanish
(e.g. "Viernes, 23 Mayo 2025"). This module converts them to timestamps
by first translating Spanish day/month names to English, then parsing
with standard strptime.
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_DAYS_SPA_ENG = {
    "Lunes": "Monday",
    "Martes": "Tuesday",
    "Miércoles": "Wednesday",
    "Jueves": "Thursday",
    "Viernes": "Friday",
    "Sábado": "Saturday",
    "Domingo": "Sunday",
}

_MONTHS_SPA_ENG = {
    "Enero": "January",
    "Febrero": "February",
    "Marzo": "March",
    "Abril": "April",
    "Mayo": "May",
    "Junio": "June",
    "Julio": "July",
    "Agosto": "August",
    "Septiembre": "September",
    "Octubre": "October",
    "Noviembre": "November",
    "Diciembre": "December",
}


def convert_spanish_date_to_timestamp(date_str: str) -> Optional[int]:
    """
    Convert a Spanish (or English) date string to epoch milliseconds.

    Expected format: "DayName, DD MonthName YYYY"
    Examples:
      "Viernes, 23 Mayo 2025" → 1747954800000
      "Friday, 23 May 2025"  → 1747954800000

    Returns None if the date cannot be parsed.
    """
    if not date_str:
        return None

    try:
        try:
            dt = datetime.strptime(date_str, "%A, %d %B %Y")
        except ValueError:
            translated = date_str
            for spa, eng in _DAYS_SPA_ENG.items():
                translated = translated.replace(spa, eng)
            for spa, eng in _MONTHS_SPA_ENG.items():
                translated = translated.replace(spa, eng)
            dt = datetime.strptime(translated, "%A, %d %B %Y")

        return int(dt.timestamp() * 1000)

    except Exception as e:
        logger.warning("Could not parse date '%s': %s", date_str, e)
        return None
