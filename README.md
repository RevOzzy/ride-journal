# Ride Journal Generator

Turn a GPX track + a folder of ride photos into a polished, self-contained HTML journal — with an interactive map, AI-curated photos, and an AI-written first-person narrative. Publish directly to WordPress with one click.

Built for adventure motorcycle riders, but works for any GPS-tracked activity: cycling, hiking, backpacking, overlanding.

---

## Features

- **Interactive map** — full GPX track on a Leaflet map with photo location pins
- **AI photo curation** — Claude Vision scores every photo and picks the best 8–20, distributed evenly across the route (no clustering at one stop)
- **AI narrative** — Claude writes a 500–700 word first-person travel story anchored to what the photos actually captured
- **Rider notes** — add your own context (place names, events, sequence) to guide the AI narrative
- **Self-contained HTML output** — photos base64-embedded, fully offline, shareable as a single file
- **Narrative editor** — edit the AI draft in-browser before publishing
- **WordPress publishing** — upload photos to your media library and publish the post in one flow
- **Add photos later** — upload additional photos from the road and append them to an existing post's gallery
- **Mixed camera support** — GPS-tagged phones matched by coordinates; cameras without GPS (Sony, Nikon, etc.) matched by timestamp
- **Match report** — shows exactly which photos matched by GPS vs. timestamp so you can diagnose placement issues

---

## Quick Start

### Option A — Docker (easiest)

```bash
git clone https://github.com/RevOzzy/ride-journal.git
cd ride-journal
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY, APP_USERNAME, APP_PASSWORD, SECRET_KEY
docker compose up
```

Open **http://localhost:5000**

### Option B — Run directly with Python

**Requirements:** Python 3.9+

```bash
git clone https://github.com/RevOzzy/ride-journal.git
cd ride-journal
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY, APP_USERNAME, APP_PASSWORD, SECRET_KEY
python app.py
```

Open **http://localhost:5000**

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Your API key from [console.anthropic.com](https://console.anthropic.com) |
| `APP_USERNAME` | Yes | Login username for the web UI |
| `APP_PASSWORD` | Yes | Login password for the web UI |
| `SECRET_KEY` | Yes | Random secret for Flask sessions — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `CULL_MODEL` | No | Claude model for photo scoring (default: `claude-sonnet-4-6`) |
| `NARRATIVE_MODEL` | No | Claude model for narrative writing (default: `claude-sonnet-4-6`) |
| `WP_URL` | No | Your WordPress site URL — leave blank to disable WP publishing |
| `WP_USERNAME` | No | WordPress username |
| `WP_APP_PASSWORD` | No | WordPress Application Password (generated in WP Admin → Users → Profile) |

### Using a local or alternative AI gateway

If you run LiteLLM, OpenRouter, or any OpenAI-compatible proxy, set:
```
ANTHROPIC_BASE_URL=http://127.0.0.1:8080
```
This lets you use free or self-hosted models for both photo culling and narrative writing.

---

## How to Use

1. **Export your GPX file** from your GPS device, cycling computer, or app (Garmin Connect, Strava, Harley-Davidson App, Komoot, etc.)
2. **Open http://localhost:5000** and log in
3. **Upload your GPX file**
4. **Select your ride photos** (JPEGs — phone and camera mixed is fine)
5. **Set the timezone offset** if your camera clock isn't in UTC (e.g. `-5` for EST, `-6` for CST). GPS-tagged phone photos don't need this.
6. **Add rider notes** (optional) — place names, highlights, sequence of events. The AI uses these as ground truth.
7. Click **Generate My Ride Journal** — takes 1–3 minutes depending on photo count
8. Review the **match report** — shows which photos were placed by GPS coordinate vs. timestamp
9. **Edit the narrative** in-browser if you want to adjust anything
10. **Download** the HTML file, or **Publish to WordPress**

---

## Photo Matching

The app tries two methods to place each photo on the route:

| Method | Camera type | How it works |
|---|---|---|
| **GPS coordinates** | iPhones, Android, GPS-enabled cameras | Matches photo GPS coords to the nearest track point within 2km |
| **Timestamp** | Sony, Nikon, Canon, any camera without GPS | Matches photo EXIF timestamp to the nearest track point in time |

**Timezone tip:** Camera clocks store local time with no timezone info. If your photos end up at mile 0 (all grouped at the start), your timezone offset is wrong. Try the offset for the timezone you were riding in.

---

## WordPress Publishing

1. Fill in `WP_URL`, `WP_USERNAME`, `WP_APP_PASSWORD` in `.env`
2. Generate an Application Password in WordPress: **Admin → Users → Profile → Application Passwords**
3. After generating a journal, click **Publish to WordPress**
4. Set a title, choose draft or publish, click go
5. Photos are uploaded to your media library and a Gutenberg gallery block is created in the post
6. To add more photos to an existing post later, use the **Add Photos** link next to the journal

---

## Cost

Each journal generation makes two types of API calls:

| Step | Model calls | Typical cost |
|---|---|---|
| Photo culling | 1 vision call per 5 photos | ~$0.03–0.15 |
| Narrative | 1 text call | ~$0.01–0.05 |
| **Total** | | **~$0.05–0.20 per journal** |

Costs vary by photo count and model. Using a free model via a local gateway brings it to $0.

---

## Project Structure

```
ride-journal/
├── app.py                  # Flask server, map builder, journal assembler
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── processor/
│   ├── gpx_parser.py       # GPX → track points + ride stats
│   ├── photo_matcher.py    # EXIF reading, GPS/timestamp matching to route
│   ├── photo_culler.py     # Claude Vision batch scoring + even distribution
│   ├── journal_writer.py   # Claude narrative writer
│   └── wp_publisher.py     # WordPress REST API — media upload + post create
├── templates/
│   ├── index.html          # Upload form + journal list
│   ├── journal.html        # Output journal template
│   ├── login.html          # Login page
│   ├── edit.html           # Narrative editor
│   ├── publish.html        # WordPress publish form
│   └── add_photos.html     # Add photos to existing WP post
└── output/                 # Generated HTML journals (gitignored)
```

---

## Troubleshooting

**"No photos matched the ride track"**
Your photo timestamps don't overlap with the GPX time range. Check that your camera date/time was correct on ride day, and try different timezone offsets.

**All photos pinned at mile 0**
Timezone offset is wrong. Set it to the UTC offset for the region you were riding in.

**WordPress upload fails**
Verify your Application Password (not your login password) is correct in `.env`. Test it with:
```bash
curl -u "username:xxxx xxxx xxxx xxxx" https://yoursite.com/wp-json/wp/v2/users/me
```

**Large photo sets time out over slow connections**
Use a local network connection or Tailscale instead of uploading over a slow remote tunnel. The app has a 2GB upload limit but your reverse proxy may have a lower one.

---

## Tech Stack

| Component | Technology |
|---|---|
| Web server | Flask |
| GPX parsing | gpxpy |
| Photo EXIF | Pillow + piexif |
| Interactive map | folium (Leaflet.js) |
| AI (vision + text) | Anthropic Claude API |
| WordPress | REST API v2 |
| Output | Self-contained HTML |

---

## License

MIT
