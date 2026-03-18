import argparse
import base64
import hashlib
import json
import math
import os
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import anthropic
import requests
import yt_dlp
from dotenv import load_dotenv

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

TOKEN_CACHE_PATH = Path(".tiktok_token")
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


def load_config():
    load_dotenv()
    client_key = os.getenv("TIKTOK_CLIENT_KEY")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if not client_key or not client_secret:
        print(
            "Error: TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET must be set in .env.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not anthropic_key:
        print("Error: ANTHROPIC_API_KEY must be set in .env.", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Post the top Twitch clips to TikTok.")
    parser.add_argument(
        "--input", default="clips.json", help="Path to clips JSON file (default: clips.json)"
    )
    parser.add_argument(
        "--count", type=int, default=3, help="Number of top clips to post (default: 3)"
    )
    args = parser.parse_args()

    return client_key, client_secret, args.input, args.count, anthropic_key


def get_top_clips(input_path: str, count: int = 3) -> tuple[str, list[dict]]:
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: '{input_path}' not found. Run main.py first.", file=sys.stderr)
        sys.exit(1)

    clips = data.get("clips", [])
    if not clips:
        print("No clips found in the input file.", file=sys.stderr)
        sys.exit(1)

    streamer_login = data.get("streamer_login", "")
    return streamer_login, clips[:count]


# --- OAuth ---

_auth_code = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = parse_qs(urlparse(self.path).query)
        _auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h2>Authorized! You can close this tab.</h2>")

    def log_message(self, format, *args):
        pass  # suppress server logs


def _load_cached_token() -> str | None:
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        cached = json.loads(TOKEN_CACHE_PATH.read_text())
        issued_at = cached.get("issued_at", 0)
        age_seconds = time.time() - issued_at
        if age_seconds < 23 * 3600:
            return cached["access_token"]
    except Exception:
        pass
    return None


def _save_token(access_token: str):
    TOKEN_CACHE_PATH.write_text(
        json.dumps({"access_token": access_token, "issued_at": time.time()})
    )


def _pkce_pair() -> tuple[str, str]:
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def authenticate_tiktok(client_key: str, client_secret: str) -> str:
    cached = _load_cached_token()
    if cached:
        print("Using cached TikTok token.")
        return cached

    code_verifier, code_challenge = _pkce_pair()
    state = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
    params = {
        "client_key": client_key,
        "scope": "video.upload,video.publish",
        "response_type": "code",
        "redirect_uri": "http://localhost:8080/callback",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = TIKTOK_AUTH_URL + "?" + urlencode(params)

    # Start local callback server in background thread
    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()

    print("\nOpening browser for TikTok authorization...")
    print(f"If it doesn't open automatically, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    thread.join(timeout=120)
    server.server_close()

    if not _auth_code:
        print("Error: Did not receive authorization code within 120 seconds.", file=sys.stderr)
        sys.exit(1)

    # Exchange code for token
    resp = requests.post(
        TIKTOK_TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": _auth_code,
            "grant_type": "authorization_code",
            "redirect_uri": "http://localhost:8080/callback",
            "code_verifier": code_verifier,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} {resp.text}")

    access_token = resp.json()["access_token"]
    _save_token(access_token)
    return access_token


# --- Caption ---

def generate_caption(clip: dict, streamer_login: str, anthropic_key: str) -> str:
    client = anthropic.Anthropic(api_key=anthropic_key)
    prompt = (
        f"Write a punchy TikTok caption for a clip from Twitch streamer '{streamer_login}'.\n"
        f"Clip title: {clip['title']}\n"
        f"Rules:\n"
        f"- Include 4-6 relevant hashtags (always include #{streamer_login} and #fyp)\n"
        f"- Total length must be under 150 characters\n"
        f"- Be exciting and conversational\n"
        f"- Output ONLY the caption text, nothing else"
    )
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )
    caption = message.content[0].text.strip()
    return caption[:150]


# --- Download ---

def download_clip(clip_url: str, clip_id: str) -> Path:
    output_path = Path(f"/tmp/twitch_clip_{clip_id}.mp4")
    if output_path.exists():
        print(f"Clip already downloaded: {output_path}")
        return output_path

    print(f"Downloading clip...")
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(output_path).replace(".mp4", ".%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([clip_url])

    # yt-dlp may name it differently — find it
    candidates = list(Path("/tmp").glob(f"twitch_clip_{clip_id}.*"))
    if not candidates:
        raise RuntimeError("yt-dlp download completed but file not found in /tmp.")
    actual = candidates[0]
    if actual != output_path:
        actual.rename(output_path)

    print(f"Downloaded to: {output_path} ({output_path.stat().st_size / 1e6:.1f} MB)")
    return output_path


# --- Upload ---

def upload_to_tiktok(video_path: Path, title: str, access_token: str, client_key: str) -> str:
    video_size = video_path.stat().st_size
    total_chunks = math.ceil(video_size / CHUNK_SIZE)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    # Step 1: Initialize
    print("Initializing TikTok upload...")
    init_body = {
        "post_info": {
            "title": title[:150],  # TikTok title max 150 chars
            "privacy_level": "SELF_ONLY",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": CHUNK_SIZE,
            "total_chunk_count": total_chunks,
        },
    }
    resp = requests.post(TIKTOK_INIT_URL, headers=headers, json=init_body, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Upload init failed: {resp.status_code} {resp.text}")

    body = resp.json()
    publish_id = body["data"]["publish_id"]
    upload_url = body["data"]["upload_url"]
    print(f"publish_id: {publish_id}")

    # Step 2: Upload chunks
    with open(video_path, "rb") as f:
        for i in range(total_chunks):
            chunk = f.read(CHUNK_SIZE)
            start = i * CHUNK_SIZE
            end = start + len(chunk) - 1
            chunk_headers = {
                "Content-Range": f"bytes {start}-{end}/{video_size}",
                "Content-Type": "video/mp4",
            }
            print(f"Uploading chunk {i + 1}/{total_chunks}...")
            put_resp = requests.put(upload_url, headers=chunk_headers, data=chunk, timeout=120)
            if put_resp.status_code not in (200, 206):
                raise RuntimeError(
                    f"Chunk {i + 1} upload failed: {put_resp.status_code} {put_resp.text}"
                )

    return publish_id


def check_publish_status(publish_id: str, access_token: str, client_key: str):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    print("Waiting for TikTok to process the video...")
    for _ in range(20):
        resp = requests.post(
            TIKTOK_STATUS_URL,
            headers=headers,
            json={"publish_id": publish_id},
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Status check failed: {resp.status_code} {resp.text}")

        status = resp.json().get("data", {}).get("status")
        print(f"  Status: {status}")
        if status == "PUBLISH_COMPLETE":
            print("\nSuccess! Clip posted to TikTok (visible in your private videos).")
            return
        if status == "FAILED":
            error = resp.json().get("data", {}).get("fail_reason", "unknown")
            raise RuntimeError(f"TikTok publish failed: {error}")
        time.sleep(3)

    print("Warning: Timed out waiting for publish confirmation. Check TikTok manually.")


def main():
    client_key, client_secret, input_path, count, anthropic_key = load_config()

    streamer_login, clips = get_top_clips(input_path, count)
    print(f"Posting top {len(clips)} clip(s) to TikTok...")

    access_token = authenticate_tiktok(client_key, client_secret)

    for i, clip in enumerate(clips, 1):
        print(f"\n--- Clip {i}/{len(clips)}: \"{clip['title']}\" — {clip['view_count']:,} views ---")
        caption = generate_caption(clip, streamer_login, anthropic_key)
        print(f"Caption: {caption}")
        video_path = download_clip(clip["url"], clip["clip_id"])
        publish_id = upload_to_tiktok(video_path, caption, access_token, client_key)
        check_publish_status(publish_id, access_token, client_key)
        if i < len(clips):
            print("Waiting 5s before next upload...")
            time.sleep(5)


if __name__ == "__main__":
    main()
