"""
Ride Journal Generator — Flask web server.
Run: python app.py
Open: http://localhost:5000
"""
import os
import uuid
import base64
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
import io

from flask import Flask, render_template, request, jsonify, send_file, url_for
from dotenv import load_dotenv
import anthropic
import folium

from processor.gpx_parser import parse_gpx
from processor.photo_matcher import match_photos_to_route
from processor.photo_culler import cull_photos
from processor.journal_writer import write_narrative

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB upload limit

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def get_anthropic_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", None)
    if base_url:
        return anthropic.Anthropic(api_key=api_key, base_url=base_url)
    return anthropic.Anthropic(api_key=api_key)


def build_folium_map(track_points: list, selected_photos: list) -> str:
    """Build a folium map and return its HTML snippet (just the map div + scripts)."""
    if not track_points:
        return "<p>No track data available.</p>"

    # Center on midpoint of track
    lats = [p["lat"] for p in track_points]
    lons = [p["lon"] for p in track_points]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    m = folium.Map(
        location=center,
        zoom_start=10,
        tiles="CartoDB positron",
        prefer_canvas=True,
    )

    # Draw GPX track
    track_coords = [(p["lat"], p["lon"]) for p in track_points]
    folium.PolyLine(
        track_coords,
        color="#E8521A",   # Harley orange
        weight=3,
        opacity=0.85,
        tooltip="Ride track",
    ).add_to(m)

    # Start marker
    start = track_points[0]
    folium.Marker(
        location=[start["lat"], start["lon"]],
        popup="<b>Start</b>",
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
    ).add_to(m)

    # End marker
    end = track_points[-1]
    folium.Marker(
        location=[end["lat"], end["lon"]],
        popup="<b>Finish</b>",
        icon=folium.Icon(color="red", icon="flag", prefix="fa"),
    ).add_to(m)

    # Photo pins
    for i, photo in enumerate(selected_photos):
        pt = photo.get("track_point", {})
        lat = pt.get("lat") or photo.get("lat")
        lon = pt.get("lon") or photo.get("lon")
        if lat is None or lon is None:
            continue
        km = photo.get("km", 0)
        miles = km * 0.621371
        popup_html = (
            f'<div style="text-align:center">'
            f'<img src="photo_{i}" id="map-photo-{i}" '
            f'style="max-width:200px;max-height:150px;border-radius:4px" '
            f'onerror="this.style.display=\'none\'" />'
            f'<br><small>Mile {miles:.0f}</small></div>'
        )
        folium.CircleMarker(
            location=[lat, lon],
            radius=7,
            color="#ffffff",
            fill=True,
            fill_color="#E8521A",
            fill_opacity=0.9,
            popup=folium.Popup(popup_html, max_width=220),
            tooltip=f"Photo at mile {miles:.0f}",
        ).add_to(m)

    # Fit bounds to track
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

    # Return just the inner HTML of the map (no full page)
    map_html = m._repr_html_()
    return map_html


def photo_to_base64_thumb(path: str, max_px: int = 1200) -> str:
    """Resize and encode photo as base64 data URI for embedding in HTML."""
    from PIL import Image
    img = Image.open(path)
    img = img.convert("RGB")

    # Auto-rotate based on EXIF orientation
    try:
        import piexif
        exif_data = img.info.get("exif")
        if exif_data:
            exif = piexif.load(exif_data)
            orientation = exif.get("0th", {}).get(piexif.ImageIFD.Orientation, 1)
            rotations = {3: 180, 6: 270, 8: 90}
            if orientation in rotations:
                img = img.rotate(rotations[orientation], expand=True)
    except Exception:
        pass

    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    b64 = base64.standard_b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def assemble_journal_html(
    stats: dict,
    narrative: str,
    map_html: str,
    selected_photos: list,
    output_path: Path,
):
    """Render the final self-contained HTML journal and write it to output_path."""
    # Encode all photos as base64
    photo_data = []
    for photo in selected_photos:
        try:
            data_uri = photo_to_base64_thumb(photo["path"])
            km = photo.get("km", 0)
            photo_data.append({
                "data_uri": data_uri,
                "km": km,
                "miles": round(km * 0.621371, 1),
                "filename": photo.get("filename", ""),
            })
        except Exception as e:
            print(f"[app] Skipping photo {photo.get('filename')}: {e}")

    # Format stats for display
    date_str = ""
    if stats.get("start_time"):
        date_str = stats["start_time"].strftime("%B %d, %Y")

    duration_str = ""
    mins = stats.get("duration_min")
    if mins:
        h, m = divmod(mins, 60)
        duration_str = f"{h}h {m:02d}m" if h else f"{m}m"

    rendered = render_template(
        "journal.html",
        date=date_str,
        distance_miles=stats.get("distance_miles", 0),
        elevation_ft=stats.get("elevation_gain_ft", 0),
        duration=duration_str,
        narrative=narrative,
        map_html=map_html,
        photos=photo_data,
    )

    output_path.write_text(rendered, encoding="utf-8")


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route("/")
def index():
    # List previously generated journals
    journals = sorted(OUTPUT_DIR.glob("*.html"), key=lambda f: f.stat().st_mtime, reverse=True)
    journal_names = [j.name for j in journals[:10]]
    return render_template("index.html", journals=journal_names)


@app.route("/generate", methods=["POST"])
def generate():
    """Handle upload and generate the journal. Returns JSON with status updates."""
    gpx_file = request.files.get("gpx_file")
    photos = request.files.getlist("photos")
    tz_offset = float(request.form.get("tz_offset", "0"))

    if not gpx_file:
        return jsonify({"error": "No GPX file uploaded"}), 400
    if not photos:
        return jsonify({"error": "No photos uploaded"}), 400

    # Save uploaded files to a temp dir
    tmp_dir = Path(tempfile.mkdtemp(prefix="ridejrnl_"))
    photo_dir = tmp_dir / "photos"
    photo_dir.mkdir()

    try:
        # Save GPX
        gpx_path = tmp_dir / "ride.gpx"
        gpx_file.save(str(gpx_path))

        # Save photos
        for photo in photos:
            if photo.filename and Path(photo.filename).suffix.lower() in {".jpg", ".jpeg"}:
                dest = photo_dir / Path(photo.filename).name
                photo.save(str(dest))

        # Step 1: Parse GPX
        gpx_data = parse_gpx(str(gpx_path))
        stats = gpx_data["stats"]
        track_points = gpx_data["points"]

        # Step 2: Match photos to route
        matched = match_photos_to_route(str(photo_dir), track_points, tz_offset_hours=tz_offset)

        if not matched:
            return jsonify({"error": "No photos could be matched to the ride track. Check that photo timestamps overlap with the GPX time range."}), 400

        # Step 3: Cull photos with AI
        client = get_anthropic_client()
        selected = cull_photos(matched, client)

        # Step 4: Write narrative
        narrative = write_narrative(stats, selected, client)

        # Step 5: Build map
        map_html = build_folium_map(track_points, selected)

        # Step 6: Assemble HTML
        ride_date = stats.get("start_time")
        date_slug = ride_date.strftime("%Y-%m-%d") if ride_date else "ride"
        filename = f"journal_{date_slug}_{uuid.uuid4().hex[:6]}.html"
        output_path = OUTPUT_DIR / filename

        assemble_journal_html(stats, narrative, map_html, selected, output_path)

        return jsonify({
            "success": True,
            "filename": filename,
            "stats": {
                "distance_miles": stats.get("distance_miles"),
                "elevation_ft": stats.get("elevation_gain_ft"),
                "duration_min": stats.get("duration_min"),
                "photos_selected": len(selected),
                "photos_total": len(matched),
            }
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/download/<filename>")
def download(filename):
    """Serve a generated journal HTML file."""
    path = OUTPUT_DIR / filename
    if not path.exists() or not path.is_file():
        return "Journal not found", 404
    # Security: ensure filename doesn't escape output dir
    if OUTPUT_DIR not in path.resolve().parents and path.resolve() != OUTPUT_DIR:
        return "Not found", 404
    return send_file(str(path), mimetype="text/html", as_attachment=False)


if __name__ == "__main__":
    print("=" * 50)
    print("  Ride Journal Generator")
    print("  Open: http://localhost:5000")
    print("=" * 50)
    app.run(debug=True, host="0.0.0.0", port=5000)
