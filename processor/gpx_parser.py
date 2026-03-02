"""
GPX Parser — extracts track points and ride statistics from a GPX file.
"""
import gpxpy
import math
from datetime import timezone


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance in km between two GPS coords."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_gpx(gpx_path: str) -> dict:
    """
    Parse a GPX file and return a dict with:
      - points: list of {lat, lon, ele, time, km}
      - stats: {distance_km, elevation_gain_m, duration_min, start_time, end_time}
    """
    with open(gpx_path, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)

    points = []
    total_km = 0.0
    elevation_gain = 0.0
    prev = None

    for track in gpx.tracks:
        for segment in track.segments:
            for pt in segment.points:
                if pt.latitude is None or pt.longitude is None:
                    continue

                # Ensure timezone-aware UTC timestamp
                t = pt.time
                if t is not None and t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)

                if prev is not None:
                    seg_km = haversine_km(prev["lat"], prev["lon"], pt.latitude, pt.longitude)
                    total_km += seg_km

                    if pt.elevation is not None and prev["ele"] is not None:
                        gain = pt.elevation - prev["ele"]
                        if gain > 0:
                            elevation_gain += gain

                entry = {
                    "lat": pt.latitude,
                    "lon": pt.longitude,
                    "ele": pt.elevation,
                    "time": t,
                    "km": round(total_km, 3),
                }
                points.append(entry)
                prev = entry

    if not points:
        raise ValueError("No valid track points found in GPX file.")

    start_time = points[0]["time"]
    end_time = points[-1]["time"]
    duration_min = None
    if start_time and end_time:
        duration_min = round((end_time - start_time).total_seconds() / 60)

    stats = {
        "distance_km": round(total_km, 2),
        "distance_miles": round(total_km * 0.621371, 2),
        "elevation_gain_m": round(elevation_gain),
        "elevation_gain_ft": round(elevation_gain * 3.28084),
        "duration_min": duration_min,
        "start_time": start_time,
        "end_time": end_time,
        "point_count": len(points),
    }

    return {"points": points, "stats": stats}


def find_nearest_point(track_points: list, target_time) -> dict | None:
    """Find the track point closest in time to target_time."""
    if not track_points or target_time is None:
        return None

    # Ensure target_time is timezone-aware
    if target_time.tzinfo is None:
        target_time = target_time.replace(tzinfo=timezone.utc)

    best = None
    best_delta = None
    for pt in track_points:
        if pt["time"] is None:
            continue
        delta = abs((pt["time"] - target_time).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = pt

    # Only match if within 6 hours (photos might be slightly off-timezone)
    if best_delta is not None and best_delta <= 6 * 3600:
        return best
    return None
