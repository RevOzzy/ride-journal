"""
WordPress Publisher — handles uploading photos and creating/updating posts
on a WordPress site via the REST API.
"""
import base64
import re
import requests
from requests.auth import HTTPBasicAuth


def _auth(username, password):
    return HTTPBasicAuth(username, password)


def upload_gpx(wp_url: str, username: str, password: str, gpx_path: str, filename: str) -> dict:
    """Upload a GPX file to WP media library. Returns {id, url}."""
    with open(gpx_path, "rb") as f:
        data = f.read()
    resp = requests.post(
        f"{wp_url}/wp-json/wp/v2/media",
        auth=_auth(username, password),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/gpx+xml",
        },
        data=data,
        timeout=30,
    )
    resp.raise_for_status()
    d = resp.json()
    return {"id": d["id"], "url": d["source_url"]}


def upload_media(wp_url: str, username: str, password: str, b64_data: str, filename: str) -> dict:
    """Upload a base64 JPEG to WP media library. Returns {id, url}."""
    img_bytes = base64.b64decode(b64_data)
    resp = requests.post(
        f"{wp_url}/wp-json/wp/v2/media",
        auth=_auth(username, password),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "image/jpeg",
        },
        data=img_bytes,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"id": data["id"], "url": data["source_url"]}


def create_post(wp_url: str, username: str, password: str, title: str,
                content: str, featured_id: int = None, status: str = "publish",
                categories: list = None, excerpt: str = None) -> dict:
    """Create a WP post. Returns {id, url}."""
    payload = {"title": title, "content": content, "status": status}
    if featured_id:
        payload["featured_media"] = featured_id
    if categories:
        payload["categories"] = categories
    if excerpt:
        payload["excerpt"] = excerpt
    resp = requests.post(
        f"{wp_url}/wp-json/wp/v2/posts",
        auth=_auth(username, password),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"id": data["id"], "url": data["link"]}


def append_gallery(wp_url: str, username: str, password: str, post_id: int, new_media: list) -> str:
    """
    Add new photos to the gallery section of an existing post.
    new_media: list of {id, url} dicts.
    Returns the post URL.
    """
    resp = requests.get(
        f"{wp_url}/wp-json/wp/v2/posts/{post_id}?context=edit",
        auth=_auth(username, password),
        timeout=30,
    )
    resp.raise_for_status()
    post_data = resp.json()
    current = post_data["content"]["raw"]
    post_url = post_data["link"]

    g_start = current.find("<!-- GALLERY_BLOCK_START -->")
    g_end = current.find("<!-- GALLERY_BLOCK_END -->")

    if g_start != -1 and g_end != -1:
        # Extract existing media from current gallery block
        existing_section = current[g_start:g_end]
        existing_ids = [int(i) for i in re.findall(r'"id":(\d+)', existing_section)]
        existing_urls = re.findall(r'<img src="([^"]+)"', existing_section)
        all_media = [{"id": i, "url": u} for i, u in zip(existing_ids, existing_urls)] + new_media
        new_gallery = "<!-- GALLERY_BLOCK_START -->\n" + _build_gallery(all_media) + "\n<!-- GALLERY_BLOCK_END -->"
        new_content = current[:g_start] + new_gallery + current[g_end + len("<!-- GALLERY_BLOCK_END -->"):]
    else:
        # No gallery yet — append one
        new_gallery = "\n\n<!-- GALLERY_BLOCK_START -->\n" + _build_gallery(new_media) + "\n<!-- GALLERY_BLOCK_END -->"
        new_content = current + new_gallery

    requests.post(
        f"{wp_url}/wp-json/wp/v2/posts/{post_id}",
        auth=_auth(username, password),
        json={"content": new_content},
        timeout=30,
    ).raise_for_status()

    return post_url


def _build_gallery(media: list) -> str:
    """Build a Gutenberg gallery block from a list of {id, url} dicts."""
    ids = [m["id"] for m in media]
    ids_str = ",".join(str(i) for i in ids)
    inner = ""
    for m in media:
        inner += (
            f'<!-- wp:image {{"id":{m["id"]},"sizeSlug":"large","linkDestination":"none"}} -->\n'
            f'<figure class="wp-block-image size-large">'
            f'<img src="{m["url"]}" alt="" class="wp-image-{m["id"]}"/></figure>\n'
            f'<!-- /wp:image -->\n'
        )
    return (
        f'<!-- wp:gallery {{"ids":[{ids_str}],"columns":3,"linkTo":"none"}} -->\n'
        f'<figure class="wp-block-gallery has-nested-images columns-3 is-cropped">\n'
        f'{inner}</figure>\n'
        f'<!-- /wp:gallery -->'
    )


def extract_journal_data(html: str) -> dict:
    """Extract metadata and content from a journal HTML file for WP publishing."""
    start = html.find("<!-- NARRATIVE_START -->")
    end = html.find("<!-- NARRATIVE_END -->")
    narrative = ""
    if start != -1 and end != -1:
        narrative = html[start + len("<!-- NARRATIVE_START -->"):end].strip()

    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    auto_title = h1.group(1).strip() if h1 else "Ride Journal"

    date_m = re.search(r'class="ride-date">(.*?)</div>', html, re.DOTALL)
    date = date_m.group(1).strip() if date_m else ""

    dist_m = re.search(r'class="val">([\d.]+)</div>\s*<div class="lbl">Miles', html)
    distance = dist_m.group(1) if dist_m else ""

    elev_m = re.search(r'class="val">([\d,]+)</div>\s*<div class="lbl">Ft Elevation', html)
    elevation = elev_m.group(1) if elev_m else ""

    dur_m = re.search(r'class="val">([^<]+)</div>\s*<div class="lbl">Ride Time', html)
    duration = dur_m.group(1).strip() if dur_m else ""

    # Extract the folium map HTML from inside the map-hero div
    map_html = ""
    map_start = html.find('<div class="map-hero">')
    map_end = html.find('<!-- Stats bar -->')
    if map_start != -1 and map_end != -1:
        inner = html[map_start + len('<div class="map-hero">'):map_end].strip()
        if inner.endswith('</div>'):
            inner = inner[:-6].strip()
        map_html = inner

    photos = re.findall(r'<img src="(data:image/jpeg;base64,[A-Za-z0-9+/=]+)"', html)

    wp_id_m = re.search(r'<!-- WP_POST_ID: (\d+) -->', html)
    wp_post_id = int(wp_id_m.group(1)) if wp_id_m else None

    return {
        "narrative": narrative,
        "auto_title": auto_title,
        "date": date,
        "distance": distance,
        "elevation": elevation,
        "duration": duration,
        "map_html": map_html,
        "photos": photos,
        "wp_post_id": wp_post_id,
    }
