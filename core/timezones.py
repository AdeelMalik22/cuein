"""Timezone helpers for business-local calendar calculations."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone


def business_day_bounds(timezone_name: str, *, now=None):
    """Return the UTC-comparable bounds of the current local business day.

    Constructing both local midnights independently keeps the range correct on
    daylight-saving transitions, where a calendar day is not always 24 hours.
    """
    business_timezone = ZoneInfo(timezone_name)
    local_date = timezone.localtime(now or timezone.now(), timezone=business_timezone).date()
    start = datetime.combine(local_date, time.min, tzinfo=business_timezone)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=business_timezone)
    return start, end
