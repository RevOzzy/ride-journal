# Ride Journal Generator — Setup

## Quick Start

### 1. Install dependencies (first time only)
```bash
cd ~/ride-journal
pip3 install -r requirements.txt --break-system-packages
```

### 2. Add your Claude API key
Edit `.env` and replace `your_api_key_here`:
```
ANTHROPIC_API_KEY=sk-ant-...
```
Get a free key at: https://console.anthropic.com

**Or use openclaw (free, no API key cost):**
Uncomment the ANTHROPIC_BASE_URL line in `.env`:
```
ANTHROPIC_BASE_URL=http://127.0.0.1:18789
```
Then set ANTHROPIC_API_KEY to any non-empty string (e.g. `local`).

### 3. Run the server
```bash
cd ~/ride-journal
python3 app.py
```
Open browser → http://localhost:5000

---

## How to Use

1. **Export GPX from Harley-Davidson app**
   - App → Routes → [your ride] → Share → Export GPX

2. **Copy ride photos** to a folder (Sony + iPhone mixed is fine)

3. **Open http://localhost:5000** in browser

4. **Drop GPX file** into the first box

5. **Select all ride photos** (click the second box, select all JPEGs)

6. **Set timezone offset** if your camera clock isn't in UTC
   - Sony cameras: usually need offset (e.g. -5 for EST)
   - iPhone: usually fine at UTC+0 (uses embedded GPS coords)

7. Click **Generate My Ride Journal** — takes 1-3 minutes

8. **Open the journal** — it's a single HTML file saved in `output/`
   - Works fully offline, no internet needed to view it
   - Share it by copying the HTML file

---

## Troubleshooting

**"No photos could be matched"**
- Check timezone offset — try a different UTC offset
- Verify your camera clock was set correctly on ride day
- Sony cameras with no GPS: timestamp matching depends on clock accuracy

**Photos in wrong location on map**
- Your GPX and camera were in different timezones — adjust tz offset

**API errors**
- Check ANTHROPIC_API_KEY in `.env`
- If using openclaw: verify `systemctl --user status openclaw-gateway.service`

---

## Cost
- Using Anthropic API directly: ~$0.05-0.20 per journal (depending on photo count)
- Using openclaw with devstral/gemini: **free**
