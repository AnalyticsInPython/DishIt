"""Distance and estimated walk time from a requester's coordinates.

Mirrors frontend/app.js's haversineMeters/distanceLabel exactly (same
formula, same 80 m/min walking-speed constant) so the number the API
returns matches what the frontend would compute on its own — the
frontend just displays what it's given once this is wired up.
"""

from __future__ import annotations

import math

WALK_M_PER_MIN = 80


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points, in meters."""
    r = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_minutes(distance_m: float) -> int:
    """Estimated walking time — straight-line distance, no routing API."""
    return max(1, round(distance_m / WALK_M_PER_MIN))
