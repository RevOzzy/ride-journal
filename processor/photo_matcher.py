"""
Photo Matcher — reads EXIF data from JPEGs and matches each photo to a
position on the GPX track by timestamp (and GPS coords when available).
"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image
import piexif

from .gpx_parser import find_nearest_point, haversine_km

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".JPG", ".JPEG"}


def _parse_exif_datetime(dt_str: str) -> datetime | None:
    """Parse EXIF datetime string 'YYYY:MM:DD HH:MM:SS' → datetime (UTC naive)."""
    try:
        return datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _exif_gps_to_decimal(gps_coord, ref: str) -> float | None:
    """Convert EXIF GPS rational tuple to decimal degrees."""
    try:
        d = gps_coord[0][0] / gps_coord[0][1]
        m = gps_coord[1][0] / gps_coord[1][1]
        s = gps_coord[2][0] / gps_coord[2][1]
        decimal = d + m / 60 + s / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal
    except (IndexError, ZeroDivisionError, TypeError):
        return None


def read_photo_exif(photo_path: str) -> dict:
    """
    Extract timestamp and GPS from a photo's EXIF data.
    Returns dict with keys: path, timestamp (datetime|None), lat (float|None), lon (float|None)
    """
    result = {
        "path": photo_path,
        "filename": os.path.basename(photo_path),
        "timestamp": None,
        "lat": None,
        "lon": None,
    }

    try:
        img = Image.open(photo_path)
        exif_data = img.info.get("exif")
        if not exif_data:
            return result

        exif_dict = piexif.load(exif_data)

        # --- Timestamp ---
        zeroth = exif_dict.get("0th", {})
        exif_ifd = exif_dict.get("Exif", {})

        raw_dt = None
        # Prefer DateTimeOriginal (when shutter fired)
        dt_bytes = exif_ifd.get(piexif.ExifIFD.DateTimeOriginal)
        if dt_bytes:
            raw_dt = _parse_exif_datetime(dt_bytes.decode("ascii", errors="ignore"))
        if raw_dt is None:
            dt_bytes = zeroth.get(piexif.ImageIFD.DateTime)
            if dt_bytes:
                raw_dt = _parse_exif_datetime(dt_bytes.decode("ascii", errors="ignore"))

        if raw_dt is not None:
            # EXIF timestamps are local time; treat as UTC for matching
            # (user can pass a tz_offset_hours if needed — Phase 2)
            result["timestamp"] = raw_dt.replace(tzinfo=timezone.utc)

        # --- GPS ---
        gps = exif_dict.get("GPS", {})
        lat_data = gps.get(piexif.GPSIFD.GPSLatitude)
        lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef)
        lon_data = gps.get(piexif.GPSIFD.GPSLongitude)
        lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef)

        if lat_data and lat_ref and lon_data and lon_ref:
            lat_ref_str = lat_ref.decode("ascii") if isinstance(lat_ref, bytes) else lat_ref
            lon_ref_str = lon_ref.decode("ascii") if isinstance(lon_ref, bytes) else lon_ref
            result["lat"] = _exif_gps_to_decimal(lat_data, lat_ref_str)
            result["lon"] = _exif_gps_to_decimal(lon_data, lon_ref_str)

    except Exception:
        pass  # Return partial result on any EXIF error

    return result


def match_photos_to_route(photo_dir: str, track_points: list, tz_offset_hours: float = 0) -> list:
    """
    Scan photo_dir for JPEGs, read EXIF, and match each to the nearest GPX point.

    tz_offset_hours: apply this offset to photo timestamps before matching
    (e.g., -5 if photos are in CDT and GPX is UTC)

    Returns list of dicts sorted by km position:
      {path, filename, timestamp, lat, lon, track_point, km, matched_by}
    """
    photo_dir = Path(photo_dir)
    photo_files = [
        f for f in photo_dir.iterdir()
        if f.is_file() and f.suffix in SUPPORTED_EXTENSIONS
    ]

    if not photo_files:
        raise ValueError(f"No JPEG photos found in {photo_dir}")

    tz_delta = timedelta(hours=tz_offset_hours)
    matched = []
    unmatched = []

    for photo_path in photo_files:
        info = read_photo_exif(str(photo_path))

        # Adjust timestamp for timezone offset
        if info["timestamp"] and tz_offset_hours != 0:
            info["timestamp"] = info["timestamp"] - tz_delta

        track_pt = None
        matched_by = None

        # Method 1: Match by GPS coords (most accurate)
        if info["lat"] is not None and info["lon"] is not None and track_points:
            best_pt = None
            best_dist = None
            for pt in track_points:
                d = haversine_km(info["lat"], info["lon"], pt["lat"], pt["lon"])
                if best_dist is None or d < best_dist:
                    best_dist = d
                    best_pt = pt
            if best_dist is not None and best_dist < 2.0:  # within 2km
                track_pt = best_pt
                matched_by = "gps"

        # Method 2: Match by timestamp
        if track_pt is None and info["timestamp"]:
            track_pt = find_nearest_point(track_points, info["timestamp"])
            if track_pt:
                matched_by = "timestamp"

        if track_pt:
            matched.append({
                **info,
                "track_point": track_pt,
                "km": track_pt["km"],
                "matched_by": matched_by,
            })
        else:
            unmatched.append(info["filename"])

    if unmatched:
        print(f"[photo_matcher] {len(unmatched)} photos could not be matched: {unmatched[:5]}{'...' if len(unmatched) > 5 else ''}")

    for p in matched:
        print(f"[photo_matcher] {p['filename']}: matched_by={p['matched_by']} km={p['km']:.1f} ts={p['timestamp']}")

    # Sort by position along route
    matched.sort(key=lambda x: x["km"])
    return matched
