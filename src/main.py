import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_USERS_URL = "https://api.twitch.tv/helix/users"
TWITCH_CLIPS_URL = "https://api.twitch.tv/helix/clips"


def load_config():
    load_dotenv()
    client_id = os.getenv("TWITCH_CLIENT_ID")
    client_secret = os.getenv("TWITCH_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Error: TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set.\n"
            "Copy .env.example to .env and fill in your Twitch app credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Fetch the most-viewed Twitch clips from the last 7 days."
    )
    parser.add_argument("--streamer", required=True, help="Twitch streamer login name")
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

    return client_id, client_secret, args.streamer.lower(), args.output, args.limit


def get_access_token(client_id: str, client_secret: str) -> str:
    resp = requests.post(
        TWITCH_TOKEN_URL,
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to get access token: {resp.status_code} {resp.text}"
        )
    return resp.json()["access_token"]


def get_broadcaster_id(login: str, token: str, client_id: str) -> str:
    headers = {"Authorization": f"Bearer {token}", "Client-Id": client_id}
    resp = requests.get(TWITCH_USERS_URL, params={"login": login}, headers=headers, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to look up streamer '{login}': {resp.status_code} {resp.text}"
        )
    data = resp.json().get("data", [])
    if not data:
        raise RuntimeError(f"Streamer '{login}' not found on Twitch.")
    return data[0]["id"]


def fetch_clips(broadcaster_id: str, token: str, client_id: str) -> list:
    headers = {"Authorization": f"Bearer {token}", "Client-Id": client_id}
    now = datetime.now(timezone.utc)
    started_at = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ended_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    clips = []
    cursor = None

    while True:
        params = {
            "broadcaster_id": broadcaster_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "first": 100,
        }
        if cursor:
            params["after"] = cursor

        resp = requests.get(TWITCH_CLIPS_URL, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to fetch clips: {resp.status_code} {resp.text}"
            )

        body = resp.json()
        clips.extend(body.get("data", []))

        cursor = body.get("pagination", {}).get("cursor")
        if not cursor:
            break

    return clips


def deduplicate_and_rank(clips: list) -> list:
    unique = {clip["id"]: clip for clip in clips}
    return sorted(unique.values(), key=lambda c: c["view_count"], reverse=True)


def write_output(
    clips: list,
    streamer_login: str,
    broadcaster_id: str,
    output_path: str,
    limit: int | None,
):
    now = datetime.now(timezone.utc)
    started_at = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ended_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    ranked = clips[:limit] if limit else clips

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "streamer_login": streamer_login,
        "broadcaster_id": broadcaster_id,
        "period": {"start": started_at, "end": ended_at},
        "total_clips": len(ranked),
        "clips": [
            {
                "clip_id": c["id"],
                "title": c["title"],
                "url": c["url"],
                "thumbnail_url": c["thumbnail_url"],
                "view_count": c["view_count"],
                "duration": c["duration"],
                "created_at": c["created_at"],
                "creator_name": c["creator_name"],
            }
            for c in ranked
        ],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(ranked)} clips to {output_path}")


def main():
    client_id, client_secret, streamer_login, output_path, limit = load_config()

    print(f"Authenticating with Twitch API...")
    token = get_access_token(client_id, client_secret)

    print(f"Resolving broadcaster ID for '{streamer_login}'...")
    broadcaster_id = get_broadcaster_id(streamer_login, token, client_id)

    print(f"Fetching clips from the last 7 days...")
    raw_clips = fetch_clips(broadcaster_id, token, client_id)

    ranked_clips = deduplicate_and_rank(raw_clips)
    print(f"Found {len(ranked_clips)} unique clips.")

    write_output(ranked_clips, streamer_login, broadcaster_id, output_path, limit)


if __name__ == "__main__":
    main()
