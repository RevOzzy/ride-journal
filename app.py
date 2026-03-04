"""
Ride Journal Generator — Flask web server.
Run: python app.py
Open: http://localhost:5000
"""
import os
import re
import uuid
import base64
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
import io
from functools import wraps

from flask import (Flask, render_template, request, jsonify, send_file,
                   url_for, session, redirect, Response, stream_with_context)
from dotenv import load_dotenv
import anthropic
import folium

from processor.gpx_parser import parse_gpx
from processor.photo_matcher import match_photos_to_route
from processor.photo_culler import cull_photos
from processor.journal_writer import write_narrative
from processor.wp_publisher import (upload_media, upload_gpx, create_post, append_gallery,
                                     extract_journal_data, _build_gallery)

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB upload limit
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def sse(data: dict) -> str:
    """Format a dict as a Server-Sent Event."""
    return f"data: {json.dumps(data)}\n\n"


def wp_env():
    return (
        os.environ.get("WP_URL", "").rstrip("/"),
        os.environ.get("WP_USERNAME", ""),
        os.environ.get("WP_APP_PASSWORD", ""),
    )


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def get_anthropic_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", None)
    if base_url:
        return anthropic.Anthropic(api_key=api_key, base_url=base_url)
    return anthropic.Anthropic(api_key=api_key)


def build_folium_map(track_points: list, selected_photos: list) -> str:
    """Build a folium map and return its HTML snippet."""
    if not track_points:
        return "<p>No track data available.</p>"

    lats = [p["lat"] for p in track_points]
    lons = [p["lon"] for p in track_points]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    m = folium.Map(location=center, zoom_start=10, tiles="CartoDB positron", prefer_canvas=True)

    folium.PolyLine(
        [(p["lat"], p["lon"]) for p in track_points],
        color="#E8521A", weight=3, opacity=0.85, tooltip="Ride track",
    ).add_to(m)

    start = track_points[0]
    folium.Marker([start["lat"], start["lon"]], popup="<b>Start</b>",
                  icon=folium.Icon(color="green", icon="play", prefix="fa")).add_to(m)

    end = track_points[-1]
    folium.Marker([end["lat"], end["lon"]], popup="<b>Finish</b>",
                  icon=folium.Icon(color="red", icon="flag", prefix="fa")).add_to(m)

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
            f'<br><small>Mile {miles:.0f}</small></div>'
        )
        folium.CircleMarker(
            location=[lat, lon], radius=7, color="#ffffff",
            fill=True, fill_color="#E8521A", fill_opacity=0.9,
            popup=folium.Popup(popup_html, max_width=220),
            tooltip=f"Photo at mile {miles:.0f}",
        ).add_to(m)

    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    return m._repr_html_()


def photo_to_base64_thumb(path: str, max_px: int = 1200) -> str:
    """Resize and encode photo as base64 data URI."""
    from PIL import Image
    img = Image.open(path).convert("RGB")
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


def assemble_journal_html(stats, narrative, map_html, selected_photos, output_path):
    """Render the final self-contained HTML journal."""
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


def _safe_path(filename):
    """Return resolved path if safe, else None."""
    path = OUTPUT_DIR / filename
    if not path.exists() or not path.is_file():
        return None
    if OUTPUT_DIR not in path.resolve().parents and path.resolve() != OUTPUT_DIR:
        return None
    return path


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form.get("username") == os.environ.get("APP_USERNAME") and
                request.form.get("password") == os.environ.get("APP_PASSWORD")):
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ──────────────────────────────────────────────
# Main pages
# ──────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    journals_raw = sorted(OUTPUT_DIR.glob("*.html"), key=lambda f: f.stat().st_mtime, reverse=True)
    journals = []
    for j in journals_raw[:10]:
        try:
            with open(j, "rb") as f:
                f.seek(max(0, j.stat().st_size - 200))
                tail = f.read().decode("utf-8", errors="ignore")
            wp_id_m = re.search(r'<!-- WP_POST_ID: (\d+) -->', tail)
            wp_post_id = int(wp_id_m.group(1)) if wp_id_m else None
        except Exception:
            wp_post_id = None
        journals.append({"name": j.name, "wp_post_id": wp_post_id})
    return render_template("index.html", journals=journals)


# ──────────────────────────────────────────────
# Generate (SSE)
# ──────────────────────────────────────────────

@app.route("/generate", methods=["POST"])
@login_required
def generate():
    """Stream journal generation progress as Server-Sent Events."""
    gpx_file = request.files.get("gpx_file")
    photos = request.files.getlist("photos")
    tz_offset = float(request.form.get("tz_offset", "0"))
    rider_notes = request.form.get("rider_notes", "")

    if not gpx_file:
        return jsonify({"error": "No GPX file uploaded"}), 400
    if not photos:
        return jsonify({"error": "No photos uploaded"}), 400

    tmp_dir = Path(tempfile.mkdtemp(prefix="ridejrnl_"))
    photo_dir = tmp_dir / "photos"
    photo_dir.mkdir()

    gpx_path = tmp_dir / "ride.gpx"
    gpx_file.save(str(gpx_path))
    for photo in photos:
        if photo.filename and Path(photo.filename).suffix.lower() in {".jpg", ".jpeg"}:
            photo.save(str(photo_dir / Path(photo.filename).name))

    def stream():
        try:
            yield sse({"step": "gpx"})
            gpx_data = parse_gpx(str(gpx_path))
            stats = gpx_data["stats"]
            track_points = gpx_data["points"]

            yield sse({"step": "match"})
            matched = match_photos_to_route(str(photo_dir), track_points, tz_offset_hours=tz_offset)
            if not matched:
                yield sse({"error": "No photos matched the ride track. Check that photo timestamps overlap with the GPX time range."})
                return

            gps_matched = [{"file": p["filename"], "km": round(p["km"], 1)} for p in matched if p.get("matched_by") == "gps"]
            ts_matched = [{"file": p["filename"], "km": round(p["km"], 1), "ts": str(p.get("timestamp", "none"))} for p in matched if p.get("matched_by") == "timestamp"]
            yield sse({"match_report": True, "gps": gps_matched, "timestamp": ts_matched})

            yield sse({"step": "cull", "photo_count": len(matched)})
            client = get_anthropic_client()
            selected = cull_photos(matched, client)

            yield sse({"step": "narrative"})
            narrative = write_narrative(stats, selected, client, rider_notes=rider_notes)

            yield sse({"step": "build"})
            map_html = build_folium_map(track_points, selected)

            ride_date = stats.get("start_time")
            date_slug = ride_date.strftime("%Y-%m-%d") if ride_date else "ride"
            filename = f"journal_{date_slug}_{uuid.uuid4().hex[:6]}.html"
            output_path = OUTPUT_DIR / filename
            assemble_journal_html(stats, narrative, map_html, selected, output_path)

            # Save a stripped GPX (no metadata/author info) alongside the journal
            gpx_out = OUTPUT_DIR / filename.replace(".html", ".gpx")
            import gpxpy, gpxpy.gpx as gpxmod
            with open(str(gpx_path), "r", encoding="utf-8") as _f:
                _src = gpxpy.parse(_f)
            _clean = gpxmod.GPX()
            for _trk in _src.tracks:
                _t = gpxmod.GPXTrack(name=_trk.name or "Ride")
                for _seg in _trk.segments:
                    _s = gpxmod.GPXTrackSegment()
                    for _pt in _seg.points:
                        _s.points.append(gpxmod.GPXTrackPoint(
                            _pt.latitude, _pt.longitude,
                            elevation=_pt.elevation, time=_pt.time
                        ))
                    _t.segments.append(_s)
                _clean.tracks.append(_t)
            gpx_out.write_text(_clean.to_xml(), encoding="utf-8")

            yield sse({
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
            yield sse({"error": str(e)})
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return Response(stream_with_context(stream()), mimetype="text/event-stream")


# ──────────────────────────────────────────────
# Edit journal narrative
# ──────────────────────────────────────────────

@app.route("/edit/<filename>")
@login_required
def edit_journal(filename):
    path = _safe_path(filename)
    if not path:
        return "Journal not found", 404
    html = path.read_text(encoding="utf-8")
    start = html.find("<!-- NARRATIVE_START -->")
    end = html.find("<!-- NARRATIVE_END -->")
    if start == -1 or end == -1:
        return "This journal doesn't support editing (regenerate it to enable).", 400
    raw = html[start + len("<!-- NARRATIVE_START -->"):end].strip()
    plain = re.sub(r"</?p>", "", raw).strip()
    paragraphs = [p.strip() for p in plain.split("\n") if p.strip()]
    text = "\n\n".join(paragraphs)
    return render_template("edit.html", filename=filename, text=text)


@app.route("/save/<filename>", methods=["POST"])
@login_required
def save_journal(filename):
    path = _safe_path(filename)
    if not path:
        return "Journal not found", 404
    text = request.form.get("narrative", "")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    new_narrative = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    html = path.read_text(encoding="utf-8")
    start = html.find("<!-- NARRATIVE_START -->")
    end = html.find("<!-- NARRATIVE_END -->")
    if start == -1 or end == -1:
        return "Markers not found", 400
    html = (
        html[:start + len("<!-- NARRATIVE_START -->")] +
        "\n      " + new_narrative + "\n      " +
        html[end:]
    )
    path.write_text(html, encoding="utf-8")
    return redirect(url_for("download", filename=filename))


# ──────────────────────────────────────────────
# Publish to WordPress (SSE)
# ──────────────────────────────────────────────

@app.route("/publish/<filename>", methods=["GET"])
@login_required
def publish_form(filename):
    path = _safe_path(filename)
    if not path:
        return "Journal not found", 404
    html = path.read_text(encoding="utf-8")
    data = extract_journal_data(html)
    return render_template("publish.html", filename=filename, **data)


@app.route("/publish/<filename>", methods=["POST"])
@login_required
def publish_journal(filename):
    path = _safe_path(filename)
    if not path:
        return jsonify({"error": "Journal not found"}), 404

    title = request.form.get("title", "Ride Journal")
    status = request.form.get("status", "publish")
    wp_url, wp_user, wp_pass = wp_env()

    html = path.read_text(encoding="utf-8")
    jdata = extract_journal_data(html)

    def stream():
        try:
            photos = jdata["photos"]
            uploaded = []

            for i, photo_uri in enumerate(photos):
                yield sse({"step": "upload", "current": i + 1, "total": len(photos)})
                m = re.match(r'data:image/jpeg;base64,(.+)', photo_uri)
                if m:
                    media = upload_media(wp_url, wp_user, wp_pass, m.group(1), f"ride-photo-{i+1:02d}.jpg")
                    uploaded.append(media)

            yield sse({"step": "creating_post"})

            # Map block
            map_block = ""
            if jdata.get("map_html"):
                map_block = (
                    '<!-- wp:html -->\n'
                    '<div style="width:100%;height:480px;overflow:hidden;">'
                    + jdata["map_html"] +
                    '</div>\n<!-- /wp:html -->\n\n'
                )

            # Stats bar block
            stats_parts = []
            if jdata["distance"]:
                stats_parts.append(
                    f'<div style="padding:.5rem 1.5rem;text-align:center;">'
                    f'<strong style="color:#E8500A;font-size:1.5rem;display:block;">{jdata["distance"]}</strong>'
                    f'<small style="text-transform:uppercase;letter-spacing:.08em;color:#aaa;">Miles</small></div>'
                )
            if jdata.get("elevation"):
                stats_parts.append(
                    f'<div style="padding:.5rem 1.5rem;text-align:center;">'
                    f'<strong style="color:#E8500A;font-size:1.5rem;display:block;">{jdata["elevation"]}</strong>'
                    f'<small style="text-transform:uppercase;letter-spacing:.08em;color:#aaa;">Ft Elevation</small></div>'
                )
            if jdata.get("duration"):
                stats_parts.append(
                    f'<div style="padding:.5rem 1.5rem;text-align:center;">'
                    f'<strong style="color:#E8500A;font-size:1.5rem;display:block;">{jdata["duration"]}</strong>'
                    f'<small style="text-transform:uppercase;letter-spacing:.08em;color:#aaa;">Ride Time</small></div>'
                )
            stats_block = ""
            if stats_parts:
                stats_block = (
                    '<!-- wp:html -->\n'
                    '<div style="background:#1a1a1a;color:#fff;display:flex;justify-content:center;'
                    'flex-wrap:wrap;gap:0;margin-bottom:1.5rem;">'
                    + "".join(stats_parts) +
                    '</div>\n<!-- /wp:html -->\n\n'
                )

            date_line = f'<p style="color:#888;font-size:.85rem;text-transform:uppercase;letter-spacing:.1em;">{jdata["date"]}</p>\n\n' if jdata["date"] else ""

            # GPX download button
            gpx_block = ""
            gpx_path = path.with_suffix(".gpx")
            if gpx_path.exists():
                yield sse({"step": "uploading_gpx"})
                try:
                    gpx_media = upload_gpx(wp_url, wp_user, wp_pass, str(gpx_path), gpx_path.name)
                    gpx_block = (
                        '\n\n<!-- wp:html -->\n'
                        '<p style="margin-top:2rem;">'
                        f'<a href="{gpx_media["url"]}" download style="display:inline-flex;align-items:center;gap:.5rem;'
                        'background:#1a1a1a;color:#fff;padding:.6rem 1.2rem;border-radius:6px;text-decoration:none;'
                        'font-size:.9rem;font-family:sans-serif;">'
                        '&#x1F5FA; Download GPX Route</a></p>\n<!-- /wp:html -->'
                    )
                except Exception as e:
                    print(f"[publish] GPX upload failed: {e}")

            gallery_block = (
                "\n\n<!-- GALLERY_BLOCK_START -->\n" +
                _build_gallery(uploaded) +
                "\n<!-- GALLERY_BLOCK_END -->"
            ) if uploaded else "\n\n<!-- GALLERY_BLOCK_START -->\n<!-- GALLERY_BLOCK_END -->"

            content = map_block + stats_block + date_line + jdata["narrative"] + gpx_block + gallery_block

            # Build excerpt from first narrative paragraph (plain text)
            first_p = re.search(r'<p>(.*?)</p>', jdata["narrative"], re.DOTALL)
            excerpt = re.sub(r'<[^>]+>', '', first_p.group(1)).strip() if first_p else ""

            featured_id = uploaded[0]["id"] if uploaded else None
            post = create_post(wp_url, wp_user, wp_pass, title, content,
                               featured_id=featured_id, status=status, categories=[2],
                               excerpt=excerpt)

            # Save post ID to journal file
            new_html = path.read_text(encoding="utf-8")
            # Remove old post ID if present, then append new one
            new_html = re.sub(r'\n?<!-- WP_POST_ID: \d+ -->', '', new_html)
            new_html += f"\n<!-- WP_POST_ID: {post['id']} -->"
            path.write_text(new_html, encoding="utf-8")

            yield sse({"success": True, "post_id": post["id"], "post_url": post["url"]})

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield sse({"error": str(e)})

    return Response(stream_with_context(stream()), mimetype="text/event-stream")


# ──────────────────────────────────────────────
# Add photos to existing WP post (SSE)
# ──────────────────────────────────────────────

@app.route("/add-photos/<filename>", methods=["GET"])
@login_required
def add_photos_form(filename):
    path = _safe_path(filename)
    if not path:
        return "Journal not found", 404
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - 200))
        tail = f.read().decode("utf-8", errors="ignore")
    wp_id_m = re.search(r'<!-- WP_POST_ID: (\d+) -->', tail)
    if not wp_id_m:
        return "This journal hasn't been published to WordPress yet.", 400
    wp_post_id = int(wp_id_m.group(1))
    return render_template("add_photos.html", filename=filename, wp_post_id=wp_post_id)


@app.route("/add-photos/<filename>", methods=["POST"])
@login_required
def add_photos_stream(filename):
    path = _safe_path(filename)
    if not path:
        return jsonify({"error": "Journal not found"}), 404

    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - 200))
        tail = f.read().decode("utf-8", errors="ignore")
    wp_id_m = re.search(r'<!-- WP_POST_ID: (\d+) -->', tail)
    if not wp_id_m:
        return jsonify({"error": "No WordPress post ID found — publish first."}), 400
    wp_post_id = int(wp_id_m.group(1))

    photos = request.files.getlist("photos")
    wp_url, wp_user, wp_pass = wp_env()

    # Save uploads to temp dir
    tmp_dir = Path(tempfile.mkdtemp(prefix="ridejrnl_add_"))
    photo_paths = []
    for photo in photos:
        if photo.filename and Path(photo.filename).suffix.lower() in {".jpg", ".jpeg"}:
            dest = tmp_dir / Path(photo.filename).name
            photo.save(str(dest))
            photo_paths.append(dest)

    def stream():
        try:
            uploaded = []
            for i, photo_path in enumerate(photo_paths):
                yield sse({"step": "upload", "current": i + 1, "total": len(photo_paths)})
                from PIL import Image
                img = Image.open(photo_path).convert("RGB")
                w, h = img.size
                if max(w, h) > 2000:
                    scale = 2000 / max(w, h)
                    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                b64 = base64.standard_b64encode(buf.getvalue()).decode()
                media = upload_media(wp_url, wp_user, wp_pass, b64, photo_path.name)
                uploaded.append(media)

            yield sse({"step": "updating_post"})
            post_url = append_gallery(wp_url, wp_user, wp_pass, wp_post_id, uploaded)

            yield sse({"success": True, "post_url": post_url, "added": len(uploaded)})

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield sse({"error": str(e)})
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return Response(stream_with_context(stream()), mimetype="text/event-stream")


# ──────────────────────────────────────────────
# Download
# ──────────────────────────────────────────────

@app.route("/download/<filename>")
@login_required
def download(filename):
    path = _safe_path(filename)
    if not path:
        return "Journal not found", 404
    return send_file(str(path), mimetype="text/html", as_attachment=False)


if __name__ == "__main__":
    print("=" * 50)
    print("  Ride Journal Generator")
    print("  Open: http://localhost:5000")
    print("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
