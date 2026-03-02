"""
Photo Culler — uses Claude Vision to score and select the best photos
from the matched set, distributed evenly across the ride.
"""
import base64
import json
import math
from pathlib import Path

import anthropic
from PIL import Image
import io


MAX_PICKS = 20
MIN_PICKS = 8
BATCH_SIZE = 5         # photos per API call (keeps prompt tokens manageable)
THUMB_MAX_PX = 1024    # resize before sending to API to save tokens


def _image_to_base64(path: str, max_px: int = THUMB_MAX_PX) -> tuple[str, str]:
    """Return (base64_data, media_type) for a photo, resized to max_px on longest side."""
    img = Image.open(path)
    img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"


def _score_batch(client: anthropic.Anthropic, photos: list) -> list[dict]:
    """
    Send a batch of photos to Claude and get scores back.
    Returns list of {index, score, reason} dicts.
    """
    content = []
    for i, photo in enumerate(photos):
        b64, mime = _image_to_base64(photo["path"])
        content.append({
            "type": "text",
            "text": f"Photo {i+1} (at km {photo['km']:.1f}):"
        })
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": b64}
        })

    content.append({
        "type": "text",
        "text": (
            "You are rating motorcycle ride photos for a travel journal. "
            "Score each photo 1-10 based on: sharpness, composition, subject interest, "
            "and how well it captures the ride experience. Penalize motion blur, "
            "underexposure, boring compositions (plain road/pavement), or duplicates. "
            "Reward: scenic vistas, interesting stops, the motorcycle in the landscape, "
            "wildlife, weather, towns, fuel stops with character.\n\n"
            "Respond ONLY with a JSON array, one object per photo:\n"
            '[{"index": 1, "score": 8, "reason": "great vista with bike in foreground"}, ...]\n'
            "No other text."
        )
    })

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": content}]
    )

    raw = resp.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    scores = json.loads(raw)
    return scores


def cull_photos(matched_photos: list, client: anthropic.Anthropic, target: int = MAX_PICKS) -> list:
    """
    Score all matched photos using Claude Vision and select the best `target` photos
    distributed evenly across the ride distance.

    Returns sorted list of selected photo dicts (same format as input, with 'score' added).
    """
    if len(matched_photos) <= target:
        # Not enough photos to cull — return all of them
        print(f"[photo_culler] Only {len(matched_photos)} photos, skipping cull (target={target})")
        for p in matched_photos:
            p["score"] = 7.0
        return matched_photos

    print(f"[photo_culler] Scoring {len(matched_photos)} photos in batches of {BATCH_SIZE}...")

    # Score all photos in batches
    scored = []
    for batch_start in range(0, len(matched_photos), BATCH_SIZE):
        batch = matched_photos[batch_start:batch_start + BATCH_SIZE]
        try:
            scores = _score_batch(client, batch)
            for s in scores:
                idx = s["index"] - 1  # 1-based → 0-based
                if 0 <= idx < len(batch):
                    photo = dict(batch[idx])
                    photo["score"] = s.get("score", 5)
                    photo["score_reason"] = s.get("reason", "")
                    scored.append(photo)
        except Exception as e:
            print(f"[photo_culler] Batch scoring error: {e} — assigning default scores")
            for photo in batch:
                p = dict(photo)
                p["score"] = 5.0
                p["score_reason"] = "scoring failed"
                scored.append(p)

    print(f"[photo_culler] Scored {len(scored)} photos. Selecting best {target}...")

    # Distribute picks across ride: divide route into `target` equal segments,
    # pick the highest-scoring photo from each segment.
    if not scored:
        return []

    max_km = max(p["km"] for p in scored)
    segment_size = max_km / target
    selected = []

    for i in range(target):
        seg_start = i * segment_size
        seg_end = (i + 1) * segment_size
        candidates = [p for p in scored if seg_start <= p["km"] < seg_end]
        if not candidates:
            continue
        best = max(candidates, key=lambda x: x["score"])
        if best["score"] >= 4:  # minimum quality threshold
            selected.append(best)

    # If we got fewer than MIN_PICKS (sparse ride), fill from remaining high-scorers
    if len(selected) < MIN_PICKS:
        already = {p["path"] for p in selected}
        extras = sorted(
            [p for p in scored if p["path"] not in already],
            key=lambda x: x["score"],
            reverse=True
        )
        for p in extras:
            if len(selected) >= MIN_PICKS:
                break
            if p["score"] >= 4:
                selected.append(p)

    selected.sort(key=lambda x: x["km"])
    print(f"[photo_culler] Selected {len(selected)} photos.")
    return selected
