from app.ingestion.hls import (
    Segment,
    best_variant_url,
    is_master_playlist,
    parse_media_playlist,
    render_playlist,
    tail_segments,
)

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=852x480,CODECS="avc1.4D401F,mp4a.40.2"
480p30/index-dvr.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080
chunked/index-dvr.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=160000
audio_only/index-dvr.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-PLAYLIST-TYPE:EVENT
#EXT-X-TWITCH-ELAPSED-SECS:0.000
#EXTINF:10.000,
0.ts
#EXTINF:10.000,
1.ts
#EXT-X-DISCONTINUITY
#EXTINF:10.000,
2-muted.ts
#EXTINF:4.500,
3.ts
"""


def test_master_playlist_detection_and_best_variant() -> None:
    assert is_master_playlist(MASTER)
    assert not is_master_playlist(MEDIA)
    assert best_variant_url(MASTER, "https://cdn.example/vod/index.m3u8") == "https://cdn.example/vod/chunked/index-dvr.m3u8"
    assert best_variant_url(MEDIA, "https://cdn.example/vod/index.m3u8") is None


def test_parse_media_playlist_resolves_urls_and_discontinuities() -> None:
    segments = parse_media_playlist(MEDIA, "https://cdn.example/vod/chunked/index-dvr.m3u8")

    assert [segment.url.rsplit("/", 1)[-1] for segment in segments] == ["0.ts", "1.ts", "2-muted.ts", "3.ts"]
    assert segments[0].url == "https://cdn.example/vod/chunked/0.ts"
    assert [segment.duration for segment in segments] == [10.0, 10.0, 10.0, 4.5]
    assert [segment.discontinuity_before for segment in segments] == [False, False, True, False]


def test_tail_segments_covers_requested_seconds_or_everything() -> None:
    segments = parse_media_playlist(MEDIA, "https://cdn.example/x.m3u8")

    assert [segment.duration for segment in tail_segments(segments, 12)] == [10.0, 4.5]
    assert [segment.duration for segment in tail_segments(segments, 14.5)] == [10.0, 4.5]
    assert [segment.duration for segment in tail_segments(segments, 15)] == [10.0, 10.0, 4.5]
    assert tail_segments(segments, 1000) == segments
    assert tail_segments(segments, 0) == []
    assert tail_segments([], 60) == []


FMP4 = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:10
#EXT-X-PLAYLIST-TYPE:EVENT
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-MAP:URI="init-0.mp4"
#EXT-X-PROGRAM-DATE-TIME:2026-10-08T19:55:52.889Z
#EXTINF:10.000,
0.mp4
#EXTINF:10.000,
1.mp4
#EXT-X-DISCONTINUITY
#EXT-X-MAP:URI="init-1.mp4"
#EXTINF:4.000,
2.mp4
"""


def test_fragmented_mp4_playlist_keeps_init_segments() -> None:
    base = "https://cdn.example/vod/1080p60/index-dvr.m3u8"
    segments = parse_media_playlist(FMP4, base)

    assert [segment.map_url for segment in segments] == [
        "https://cdn.example/vod/1080p60/init-0.mp4",
        "https://cdn.example/vod/1080p60/init-0.mp4",
        "https://cdn.example/vod/1080p60/init-1.mp4",
    ]

    # The rendered tail must start with the init segment its first fragment needs,
    # and repeat the tag only when the map changes.
    lines = render_playlist(tail_segments(segments, 14)).splitlines()
    assert lines[1] == "#EXT-X-VERSION:6"
    assert lines.index('#EXT-X-MAP:URI="https://cdn.example/vod/1080p60/init-0.mp4"') < lines.index(
        "https://cdn.example/vod/1080p60/1.mp4"
    )
    assert lines.index('#EXT-X-MAP:URI="https://cdn.example/vod/1080p60/init-1.mp4"') == lines.index(
        "https://cdn.example/vod/1080p60/2.mp4"
    ) - 2
    assert lines.count("#EXT-X-MAP:URI=") == 0  # no empty maps
    assert sum(line.startswith("#EXT-X-MAP:") for line in lines) == 2

    only_first = render_playlist(segments[:2]).splitlines()
    assert sum(line.startswith("#EXT-X-MAP:") for line in only_first) == 1
    assert "#EXT-X-VERSION:3" in render_playlist(parse_media_playlist(MEDIA, base)).splitlines()


def test_render_playlist_is_a_complete_vod_playlist() -> None:
    text = render_playlist(
        [
            Segment(url="https://cdn.example/1.ts", duration=10.0),
            Segment(url="https://cdn.example/2.ts", duration=4.5, discontinuity_before=True),
        ]
    )

    lines = text.splitlines()
    assert lines[0] == "#EXTM3U"
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in lines
    assert "#EXT-X-TARGETDURATION:10" in lines
    assert lines[-1] == "#EXT-X-ENDLIST"
    assert lines.index("#EXT-X-DISCONTINUITY") == lines.index("https://cdn.example/2.ts") - 2
    assert "#EXTINF:4.500," in lines
    # Round-trips through the parser.
    parsed = parse_media_playlist(text, "https://other.example/")
    assert [segment.url for segment in parsed] == ["https://cdn.example/1.ts", "https://cdn.example/2.ts"]
    assert parsed[1].discontinuity_before
