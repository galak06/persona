"""Jamendo music client — search instrumental tracks and download an mp3.

Public catalog read only (no OAuth). Requires `JAMENDO_CLIENT_ID` in the env
(per the project rule: secrets live in `.claude/settings.local.json`).

The returned mp3 path is fed to `compose_reel(audio_path=…)` which apads/atrims
it to the video length — no need to pick a track of an exact duration, just
prefer tracks with a good opening.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.jamendo.com/v3.0"
_DEFAULT_TAGS = "acoustic+happy+upbeat"
_MIN_DURATION_S = 20  # must be at least as long as our Reel
_MAX_DURATION_S = 240
_SEARCH_LIMIT = 20


class MusicError(RuntimeError):
    """Raised on a failed Jamendo search or download."""


@dataclass
class Track:
    id: str
    name: str
    artist: str
    duration_s: int
    download_url: str


def search_tracks(
    tags: str = _DEFAULT_TAGS,
    *,
    limit: int = _SEARCH_LIMIT,
    min_duration_s: int = _MIN_DURATION_S,
    max_duration_s: int = _MAX_DURATION_S,
) -> list[Track]:
    """Return instrumental tracks matching `tags`, filtered by duration."""
    client_id = os.environ.get("JAMENDO_CLIENT_ID")
    if not client_id:
        raise MusicError("JAMENDO_CLIENT_ID not set — add it to settings.local.json")

    params = {
        "client_id": client_id,
        "format": "json",
        "limit": str(limit),
        "tags": tags,
        "vocalinstrumental": "instrumental",
        "durationbetween": f"{min_duration_s}_{max_duration_s}",
        "order": "popularity_total",
        "audioformat": "mp32",
    }
    logger.info("jamendo search tags=%r limit=%d", tags, limit)
    r = httpx.get(f"{_API_ROOT}/tracks/", params=params, timeout=30.0)
    if r.status_code >= 400:
        raise MusicError(f"jamendo search HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    if body.get("headers", {}).get("status") != "success":
        raise MusicError(f"jamendo search failed: {body.get('headers')}")

    tracks = [
        Track(
            id=str(t["id"]),
            name=t.get("name", ""),
            artist=t.get("artist_name", ""),
            duration_s=int(t.get("duration", 0)),
            download_url=t.get("audiodownload", ""),
        )
        for t in body.get("results", [])
        if t.get("audiodownload")
    ]
    if not tracks:
        raise MusicError(f"jamendo returned no tracks for tags={tags!r}")
    return tracks


def pick_track(tracks: list[Track]) -> Track:
    """Random pick from the top 5 results for variety across posts."""
    pool = tracks[:5] if len(tracks) >= 5 else tracks
    return random.choice(pool)


def download_track(track: Track, output_path: Path) -> Path:
    """Download the mp3 for `track` to `output_path`. Returns the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("jamendo download id=%s → %s", track.id, output_path)
    with httpx.stream("GET", track.download_url, timeout=120.0, follow_redirects=True) as r:
        if r.status_code >= 400:
            raise MusicError(f"jamendo download HTTP {r.status_code} for id={track.id}")
        with open(output_path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return output_path


def get_music_for_reel(
    output_path: Path,
    *,
    tags: str = _DEFAULT_TAGS,
) -> Track:
    """One-call helper: search → pick → download. Returns the track metadata."""
    tracks = search_tracks(tags=tags)
    chosen = pick_track(tracks)
    download_track(chosen, output_path)
    return chosen
