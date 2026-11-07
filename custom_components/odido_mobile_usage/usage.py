"""Normalized app usage model and Dutch number parsing."""

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation


class UsageParseError(ValueError):
    """Invalid presented number."""


def number(value):
    """Dutch presentation: 10.000 = ten thousand, 10,5 = ten and a half."""
    try:
        result = Decimal(value.replace(".", "").replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        raise UsageParseError("Invalid numeric value") from None
    if not result.is_finite() or result < 0:
        raise UsageParseError("Invalid numeric value")
    return result


@dataclass(frozen=True)
class UsageData:
    used_gb: float
    remaining_gb: float
    limit_gb: float
    percentage: float
    voice_used_minutes: float | None = None
    voice_allowance: str | None = None
    extra_costs: float | None = None
    reported_data_used_gb: float | None = None
    sms_used: int | None = None

    def as_dict(self):
        return asdict(self)
