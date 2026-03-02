"""
Journal Writer — sends ride data to Claude and gets back a first-person
travel narrative ready to embed in the HTML output.
"""
import anthropic


def _format_duration(minutes: int | None) -> str:
    if minutes is None:
        return "unknown duration"
    h = minutes // 60
    m = minutes % 60
    if h == 0:
        return f"{m} minutes"
    return f"{h}h {m}m" if m else f"{h} hours"


def _format_date(dt) -> str:
    if dt is None:
        return "unknown date"
    try:
        return dt.strftime("%B %d, %Y")
    except Exception:
        return str(dt)


def write_narrative(stats: dict, selected_photos: list, client: anthropic.Anthropic) -> str:
    """
    Ask Claude to write a first-person ride narrative.

    stats: from gpx_parser (distance_miles, elevation_gain_ft, duration_min, start_time, etc.)
    selected_photos: list with 'score_reason' and 'km' for each selected photo
    Returns: HTML string (paragraphs wrapped in <p> tags)
    """
    date_str = _format_date(stats.get("start_time"))
    distance = stats.get("distance_miles", 0)
    elevation = stats.get("elevation_gain_ft", 0)
    duration = _format_duration(stats.get("duration_min"))

    # Build a summary of what the photos captured
    photo_notes = []
    for i, p in enumerate(selected_photos, 1):
        reason = p.get("score_reason", "")
        km = p.get("km", 0)
        miles = km * 0.621371
        if reason:
            photo_notes.append(f"  - At mile {miles:.0f}: {reason}")

    photo_summary = "\n".join(photo_notes) if photo_notes else "  (no photo descriptions available)"

    prompt = f"""You are writing a first-person travel journal entry for a motorcycle ride on a Harley-Davidson Pan America adventure bike.

Ride data:
- Date: {date_str}
- Total distance: {distance:.0f} miles
- Elevation gain: {elevation:,} feet
- Ride time: {duration}
- Number of photos taken: {len(selected_photos)}

Photos captured along the way (use these to anchor specific moments):
{photo_summary}

Write a vivid, authentic first-person narrative about this ride — 500-700 words. Requirements:
- Sound like a real rider wrote it, not a travel brochure
- Reference specific moments from the photo descriptions above
- Include sensory details: wind, sound of the engine, smell of the road, weather feel
- Mention the Pan America by feel/character (its weight, the wind protection, ADV capability) without being a sales pitch
- Cover the arc of the day: start, middle moments, arrival feeling
- Be personal and specific, not generic ("beautiful scenery" is lazy — describe what you actually saw)
- End with a reflection — what this kind of riding means

Format: Return only the narrative text, split into 4-6 paragraphs. Each paragraph should be wrapped in <p> tags. No headers, no title, no markdown."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    narrative = resp.content[0].text.strip()

    # Ensure paragraphs are wrapped in <p> tags
    if not narrative.startswith("<p>"):
        paragraphs = [p.strip() for p in narrative.split("\n\n") if p.strip()]
        narrative = "\n".join(f"<p>{p}</p>" for p in paragraphs)

    return narrative
