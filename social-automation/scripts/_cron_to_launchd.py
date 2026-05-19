"""Cron -> launchd StartCalendarInterval translator.

Split out of ``regenerate_plists.py`` so the script stays under the
project's 300-line ceiling. Exposes:

- ``UnsupportedCronError`` — ValueError subclass for cron shapes we
  can't represent in launchd.
- ``cron_to_launchd(cron)`` — returns a dict (single fire-time) or a
  list of dicts (one per fire-time, e.g. an hour range).

Supported shapes (5-field cron):

- ``M H * * *``       -> ``{"Hour": H, "Minute": M}``
- ``M H D * *``       -> ``{"Day": D, "Hour": H, "Minute": M}``
- ``M H * * W``       -> ``{"Weekday": W, "Hour": H, "Minute": M}``
- ``M H D * W``       -> ``{"Day": D, "Weekday": W, "Hour": H, "Minute": M}``
- ``M H-H * * *``     -> ``[{"Hour": h, "Minute": M}, ...]`` (range)
- ``M H,H,H * * *``   -> ``[{"Hour": h, "Minute": M}, ...]`` (list)

Anything else (steps like ``*/5``, complex minute, non-* month, mixed
range+list day-of-week) raises ``UnsupportedCronError``.
"""
from __future__ import annotations


class UnsupportedCronError(ValueError):
    """Raised when a cron expression uses syntax we don't translate to launchd."""


def _parse_hour_field(field: str) -> list[int]:
    if "-" in field:
        lo_s, hi_s = field.split("-", 1)
        lo, hi = int(lo_s), int(hi_s)
        if lo > hi:
            raise UnsupportedCronError(f"hour range reversed: {field}")
        return list(range(lo, hi + 1))
    if "," in field:
        return [int(part) for part in field.split(",")]
    return [int(field)]


def _validate_no_step(field: str, name: str) -> None:
    if "/" in field:
        raise UnsupportedCronError(f"step expressions not supported in {name}: {field}")


def _ordered_calendar(d: dict[str, int]) -> dict[str, int]:
    """Reorder calendar-interval keys to Day -> Weekday -> Hour -> Minute."""
    order = ("Day", "Weekday", "Hour", "Minute")
    return {k: d[k] for k in order if k in d}


def cron_to_launchd(cron: str) -> dict[str, int] | list[dict[str, int]]:
    """Translate a 5-field cron expression to launchd's StartCalendarInterval."""
    parts = cron.strip().split()
    if len(parts) != 5:
        raise UnsupportedCronError(f"expected 5 cron fields, got {len(parts)}: {cron!r}")
    minute_s, hour_s, dom_s, month_s, dow_s = parts

    for name, field in (
        ("minute", minute_s),
        ("month", month_s),
        ("dow", dow_s),
        ("dom", dom_s),
        ("hour", hour_s),
    ):
        _validate_no_step(field, name)

    if month_s != "*":
        raise UnsupportedCronError(f"non-* month field not supported: {cron!r}")
    if "," in minute_s or "-" in minute_s or minute_s == "*":
        raise UnsupportedCronError(f"complex minute field not supported: {minute_s!r}")
    minute = int(minute_s)

    has_dom = dom_s != "*"
    has_dow = dow_s != "*"
    if has_dom and ("," in dom_s or "-" in dom_s):
        raise UnsupportedCronError(f"complex day-of-month not supported: {dom_s!r}")
    if has_dow and ("," in dow_s or "-" in dow_s):
        raise UnsupportedCronError(f"complex day-of-week not supported: {dow_s!r}")

    if hour_s == "*":
        raise UnsupportedCronError(f"wildcard hour not supported: {cron!r}")
    hours = _parse_hour_field(hour_s)

    base: dict[str, int] = {"Minute": minute}
    if has_dom:
        base["Day"] = int(dom_s)
    if has_dow:
        base["Weekday"] = int(dow_s)

    if len(hours) == 1:
        return _ordered_calendar({**base, "Hour": hours[0]})
    return [_ordered_calendar({**base, "Hour": h}) for h in hours]


__all__ = ["UnsupportedCronError", "cron_to_launchd"]
