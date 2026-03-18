import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import cloudscraper

KICK_CHANNEL_CLIPS_URL = "https://kick.com/api/v2/channels/{slug}/clips"


def load_config():
    parser = argparse.ArgumentParser(
        description="Fetch the most-viewed Kick clips from the last 7 days."
    )
    parser.add_argument("--streamer", required=True, help="Kick channel slug (username)")
    parser.add_argument(
        "--output", default="clips.json", help="Output JSON file path (default: clips.json)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of clips to include in output (default: all)",
    )
    args = parser.parse_args()
    return args.streamer.lower(), args.output, args.limit


def fetch_clips(slug: str) -> list:
    url = KICK_CHANNEL_CLIPS_URL.format(slug=slug)
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(url, timeout=30)
    if resp.status_code == 404:
        raise RuntimeError(f"Channel '{slug}' not found on Kick.")
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to fetch clips for '{slug}': {resp.status_code} {resp.text}"
        )
    body = resp.json()
    return body.get("clips", [])


def filter_last_7_days(clips: list) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    result = []
    for clip in clips:
        created_raw = clip.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if created_dt >= cutoff:
            result.append(clip)
    return result


def deduplicate_and_rank(clips: list) -> list:
    unique = {clip["id"]: clip for clip in clips}
    return sorted(unique.values(), key=lambda c: c.get("views", 0), reverse=True)


def write_output(clips: list, slug: str, output_path: str, limit: int | None):
    now = datetime.now(timezone.utc)
    started_at = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ended_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    ranked = clips[:limit] if limit else clips

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "streamer_login": slug,
        "platform": "kick",
        "period": {"start": started_at, "end": ended_at},
        "total_clips": len(ranked),
        "clips": [
            {
                "clip_id": c["id"],
                "title": c.get("title", ""),
                "url": c.get("clip_url", c.get("video_url", "")),
                "thumbnail_url": c.get("thumbnail_url", ""),
                "view_count": c.get("views", 0),
                "duration": c.get("duration", 0),
                "created_at": c.get("created_at", ""),
                "creator_name": (c.get("creator") or {}).get("username", ""),
            }
            for c in ranked
        ],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(ranked)} clips to {output_path}")


def main():
    slug, output_path, limit = load_config()

    print(f"Fetching clips for '{slug}' from Kick...")
    raw_clips = fetch_clips(slug)
    print(f"Retrieved {len(raw_clips)} total clips.")

    recent_clips = filter_last_7_days(raw_clips)
    print(f"Filtered to {len(recent_clips)} clips from the last 7 days.")

    ranked_clips = deduplicate_and_rank(recent_clips)

    write_output(ranked_clips, slug, output_path, limit)


if __name__ == "__main__":
    main()
