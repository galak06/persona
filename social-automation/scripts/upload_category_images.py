"""Upload category JPEG images to the WordPress media library.

Reads WP_URL, WP_USER, WP_APP_PASSWORD from environment.
For each image: checks if a media item with the same title already exists
(idempotent); if found, returns the existing URL; otherwise uploads and
returns the new source_url.

Usage:
    python scripts/upload_category_images.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

CATEGORIES_DIR = Path("/Users/gilcohen/Desktop/Clean images/categories")

CATEGORY_FILES: dict[str, str] = {
    "Grooming": "Grooming.jpeg",
    "Food & Diet": "Food & Diet.jpeg",
    "Lifestyle & Gear": "Lifestyle & Gear.jpeg",
    "Training": "Training.jpeg",
}


def _auth() -> HTTPBasicAuth:
    wp_user = os.environ.get("WP_USER", "")
    wp_pass = os.environ.get("WP_APP_PASSWORD", "")
    if not wp_user or not wp_pass:
        print("ERROR: WP_USER and/or WP_APP_PASSWORD not set in environment.", file=sys.stderr)
        sys.exit(1)
    return HTTPBasicAuth(wp_user, wp_pass)


def _base_url() -> str:
    url = os.environ.get("WP_URL", "").rstrip("/")
    if not url:
        print("ERROR: WP_URL not set in environment.", file=sys.stderr)
        sys.exit(1)
    return url


def check_existing(base_url: str, auth: HTTPBasicAuth, title: str) -> str | None:
    """Return source_url if a media item with this title already exists, else None."""
    resp = requests.get(
        f"{base_url}/wp-json/wp/v2/media",
        params={"search": title, "per_page": 5},
        auth=auth,
        timeout=30,
    )
    resp.raise_for_status()
    items: list[dict] = resp.json()
    for item in items:
        rendered_title: str = item.get("title", {}).get("rendered", "")
        if rendered_title.strip().lower() == title.strip().lower():
            return item.get("source_url", "")
    return None


def upload_image(
    base_url: str,
    auth: HTTPBasicAuth,
    category_name: str,
    file_path: Path,
) -> str:
    """Upload image and return source_url. Raises on failure."""
    with file_path.open("rb") as fh:
        resp = requests.post(
            f"{base_url}/wp-json/wp/v2/media",
            auth=auth,
            files={"file": (file_path.name, fh, "image/jpeg")},
            data={
                "title": category_name,
                "alt_text": f"{category_name} category",
            },
            timeout=60,
        )
    if resp.status_code == 201:
        return resp.json()["source_url"]
    raise RuntimeError(
        f"Upload failed for '{category_name}': HTTP {resp.status_code} — {resp.text[:300]}"
    )


def main() -> None:
    base_url = _base_url()
    auth = _auth()

    results: dict[str, str] = {}

    for category_name, filename in CATEGORY_FILES.items():
        file_path = CATEGORIES_DIR / filename
        if not file_path.exists():
            print(f"WARNING: file not found — {file_path}", file=sys.stderr)
            results[category_name] = "FILE_NOT_FOUND"
            continue

        print(f"Processing: {category_name} ({filename})")

        existing_url = check_existing(base_url, auth, category_name)
        if existing_url:
            print(f"  → Already exists, skipping upload. URL: {existing_url}")
            results[category_name] = existing_url
        else:
            url = upload_image(base_url, auth, category_name, file_path)
            print(f"  → Uploaded successfully. URL: {url}")
            results[category_name] = url

    print("\n--- Summary ---")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
