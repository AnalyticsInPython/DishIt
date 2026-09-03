from math import atan2, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6371000
METERS_PER_DEG_LAT = 111320.0


def haversine_distance_m(lat1, lng1, lat2, lng2):
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lng2 - lng1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * atan2(sqrt(a), sqrt(1 - a))


def offset_latlng(lat, lng, north_m, east_m):
    d_lat = north_m / METERS_PER_DEG_LAT
    d_lng = east_m / (METERS_PER_DEG_LAT * cos(radians(lat)))
    return lat + d_lat, lng + d_lng


def grid_points(lat, lng, radius_m, spacing_m):
    """Anchor points tiling the circle. A maps search ranks results around whichever
    point you search from, so a single anchor never enumerates the whole radius."""
    if spacing_m <= 0:
        return [(lat, lng)]
    points = []
    steps = int(radius_m // spacing_m)
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            north, east = i * spacing_m, j * spacing_m
            if sqrt(north**2 + east**2) <= radius_m:
                points.append(offset_latlng(lat, lng, north, east))
    return points
