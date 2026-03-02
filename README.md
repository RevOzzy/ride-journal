# Ride Journal Generator

A local web tool that turns a GPX file + a folder of ride photos into a beautiful, self-contained HTML journal — with an interactive map, AI-selected best photos, and an AI-written first-person narrative.

Built for the **Harley-Davidson Pan America** (Sony camera + iPhone photos).

![Screenshot placeholder](https://via.placeholder.com/900x400/1a1a1a/E8521A?text=Ride+Journal+Generator)

---

## Features

- **Interactive map** — full GPX track plotted on a Leaflet map with photo pins
- **AI photo curation** — Claude Vision scores all your photos and picks the best 15-20, distributed evenly across the ride (no clustering at one stop)
- **AI narrative** — Claude writes a 500-700 word first-person ride story anchored to what the photos actually captured
- **Self-contained HTML output** — photos base64-embedded, works fully offline, shareable as a single file
- **Handles mixed cameras** — Sony DSLR/mirrorless (timestamp matching) + iPhone (GPS coord matching)

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/RevOzzy/ride-journal.git
cd ride-journal
pip3 install -r requirements.txt
```

### 2. Set your API key

Edit `.env`:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```
Get a free key at [console.anthropic.com](https://console.anthropic.com).

> **Running locally with openclaw?** Uncomment `ANTHROPIC_BASE_URL=http://127.0.0.1:18789` in `.env` and set the key to any non-empty string to use free local models instead.

### 3. Run

```bash
python3 app.py
```

Open **http://localhost:5000** in your browser.

---

## How to Use

1. **Export GPX** from the Harley-Davidson app → Routes → \[your ride\] → Share → Export GPX
2. **Drop the GPX file** into the first upload box
3. **Select all ride photos** (JPEGs from Sony + iPhone, mixed fine)
4. **Set timezone offset** if your Sony clock isn't in UTC (iPhones auto-detect via GPS)
5. Click **Generate My Ride Journal** — takes 1-3 minutes
6. Open and save the finished HTML file

---

## Project Structure

```
ride-journal/
├── app.py                  # Flask server, map builder, HTML assembler
├── requirements.txt
├── .env                    # API key (not committed)
├── processor/
│   ├── gpx_parser.py       # GPX → track points + ride stats
│   ├── photo_matcher.py    # EXIF reading, GPS/timestamp matching
│   ├── photo_culler.py     # Claude Vision batch scoring + selection
│   └── journal_writer.py   # Claude narrative writer
├── templates/
│   ├── index.html          # Upload UI
│   └── journal.html        # Output journal template
└── output/                 # Generated HTML journals saved here
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web UI | Flask (local server) |
| GPX Parsing | gpxpy |
| Photo EXIF | Pillow + piexif |
| Map | folium (Leaflet.js) |
| AI | Claude API (claude-sonnet-4-6) |
| Output | Self-contained HTML |

---

## Cost

- **Anthropic API directly**: ~$0.05–0.20 per journal (varies by photo count)
- **openclaw with free models**: $0

---

## Troubleshooting

**"No photos could be matched to the ride track"**
Try a different timezone offset. Sony cameras store local time with no timezone info — if your camera was set to CDT, use UTC-5.

**Photos pinned in wrong location on map**
Same fix — adjust the timezone offset in the UI.

**API errors**
Check `ANTHROPIC_API_KEY` in `.env`. If using openclaw, verify the gateway is running:
`systemctl --user status openclaw-gateway.service`

---

## Roadmap (Phase 2)

- [ ] Deploy to goadventureride.com as a public ride journal site
- [ ] WordPress publishing integration
- [ ] PDF export
- [ ] Social media captions generator
- [ ] Mobile upload support
