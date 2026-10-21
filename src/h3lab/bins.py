"""16-day binning for the regional time axis.

The regional build uses a continuous 16-day grid starting at EPOCH,
independent of season definitions used in the pilot.
"""

from __future__ import annotations

from datetime import date, timedelta


EPOCH = date(2000, 1, 1)
BIN_DAYS = 16


def bin_index(d: date | str) -> int:
    if isinstance(d, str):
        # Handle ISO format
        if "T" in d:
            d = d.split("T")[0]
        d = date.fromisoformat(d)
    delta = (d - EPOCH).days
    return delta // BIN_DAYS


def bin_centre(b: int) -> date:
    return EPOCH + timedelta(days=b * BIN_DAYS + BIN_DAYS // 2)


def bin_start(b: int) -> date:
    return EPOCH + timedelta(days=b * BIN_DAYS)


def bin_end(b: int) -> date:
    # inclusive end of bin is start + BIN_DAYS - 1
    return bin_start(b) + timedelta(days=BIN_DAYS - 1)


def bins_for_year(year: int) -> list[int]:
    """All bins that overlap the given calendar year."""
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    b0 = bin_index(start)
    b1 = bin_index(end)
    # Also include bins that might start in previous year but overlap
    # bin_index gives the bin containing start
    bins = []
    b = b0
    while bin_start(b) <= end:
        bins.append(b)
        b += 1
        if b > b1 + 5:  # safety
            break
    # Deduplicate and sort
    return sorted(set(bins))


def is_wet_season_date(d: date | str) -> bool:
    """True for a date in the May-October growing season.

    The season is a property of the *date*, never of the bin number (D-18):
    the 16-day grid is continuous and its phase precesses through the
    calendar, so `bin_index mod 23` carries no seasonal meaning at all.
    """
    if isinstance(d, str):
        if "T" in d:
            d = d.split("T")[0]
        d = date.fromisoformat(d)
    return 5 <= d.month <= 10


def is_dry_season_date(d: date | str) -> bool:
    """True for a date outside the growing season -- the complement of
    `is_wet_season_date`, so November through April.

    This is deliberately *not* the same window as the pilot's dry season
    (1 November - 31 December, `config.SEASONS`). The two products are
    compared on the pilot's window when the D-18 acceptance check runs,
    because the pilot is the independent reference (D-28).
    """
    if isinstance(d, str):
        if "T" in d:
            d = d.split("T")[0]
        d = date.fromisoformat(d)
    return not is_wet_season_date(d)
