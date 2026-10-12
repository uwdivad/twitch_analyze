"""Minimal HLS playlist helpers for cutting the tail of a VOD.

Only what the clip feature needs: tell master and media playlists apart, pick the
best variant, list media segments with durations, and write a self-contained local
playlist (absolute segment URLs, ``EXT-X-ENDLIST``) that ffmpeg can download with
``-c copy``. Writing our own playlist sidesteps the "live" handling ffmpeg applies
to an in-progress VOD playlist (no ``ENDLIST``), which would otherwise start at the
live edge instead of at our offset.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

__all__ = [
    "Segment",
    "best_variant_url",
    "is_master_playlist",
    "parse_media_playlist",
    "render_playlist",
    "tail_segments",
]


@dataclass(frozen=True)
class Segment:
    url: str
    duration: float
    discontinuity_before: bool = False
    # Initialization section (``EXT-X-MAP``) the segment needs; fragmented-MP4 VODs
    # (Twitch's newer 1080p60 archives) cannot be demuxed without it.
    map_url: str | None = None


def _attribute(line: str, name: str) -> str | None:
    """Read one ``NAME=value`` attribute from an HLS tag line (quoted or bare)."""
    key = f"{name}="
    start = line.find(key)
    if start < 0:
        return None
    rest = line[start + len(key) :]
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end > 0 else rest[1:]
    end = rest.find(",")
    return rest if end < 0 else rest[:end]


def is_master_playlist(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in text


def best_variant_url(text: str, base_url: str) -> str | None:
    """URL of the highest-bandwidth variant in a master playlist (absolute)."""
    best_url: str | None = None
    best_bandwidth = -1
    pending_bandwidth: int | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            value = _attribute(line, "BANDWIDTH")
            try:
                pending_bandwidth = int(value) if value is not None else 0
            except ValueError:
                pending_bandwidth = 0
            continue
        if line.startswith("#"):
            continue
        if pending_bandwidth is not None:
            if pending_bandwidth > best_bandwidth:
                best_bandwidth = pending_bandwidth
                best_url = urljoin(base_url, line)
            pending_bandwidth = None
    return best_url


def parse_media_playlist(text: str, base_url: str) -> list[Segment]:
    """Segments of a media playlist in order, with absolute URLs."""
    segments: list[Segment] = []
    duration: float | None = None
    discontinuity = False
    map_url: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            value = line[len("#EXTINF:") :].split(",", 1)[0].strip()
            try:
                duration = float(value)
            except ValueError:
                duration = None
            continue
        if line.startswith("#EXT-X-DISCONTINUITY"):
            discontinuity = True
            continue
        if line.startswith("#EXT-X-MAP:"):
            uri = _attribute(line, "URI")
            map_url = urljoin(base_url, uri) if uri else None
            continue
        if line.startswith("#"):
            continue
        if duration is None:
            # A URI without a preceding EXTINF is not a media segment we can time.
            continue
        segments.append(
            Segment(
                url=urljoin(base_url, line),
                duration=max(duration, 0.0),
                discontinuity_before=discontinuity,
                map_url=map_url,
            )
        )
        duration = None
        discontinuity = False
    return segments


def tail_segments(segments: list[Segment], seconds: float) -> list[Segment]:
    """Shortest suffix of ``segments`` whose total duration is at least ``seconds``.

    Returns every segment when the playlist is shorter than requested.
    """
    if seconds <= 0 or not segments:
        return []
    total = 0.0
    start = len(segments)
    while start > 0 and total < seconds:
        start -= 1
        total += segments[start].duration
    return segments[start:]


def render_playlist(segments: list[Segment]) -> str:
    """A complete VOD media playlist for the given segments."""
    target = max((int(segment.duration + 0.999) for segment in segments), default=1)
    uses_map = any(segment.map_url for segment in segments)
    lines = [
        "#EXTM3U",
        # EXT-X-MAP needs protocol version 5 or later.
        f"#EXT-X-VERSION:{6 if uses_map else 3}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        f"#EXT-X-TARGETDURATION:{max(target, 1)}",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    current_map: str | None = None
    for segment in segments:
        if segment.discontinuity_before:
            lines.append("#EXT-X-DISCONTINUITY")
        if segment.map_url and segment.map_url != current_map:
            lines.append(f'#EXT-X-MAP:URI="{segment.map_url}"')
            current_map = segment.map_url
        lines.append(f"#EXTINF:{segment.duration:.3f},")
        lines.append(segment.url)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"
