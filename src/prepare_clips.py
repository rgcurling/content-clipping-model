import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic
import yt_dlp
from dotenv import load_dotenv

OUTPUT_DIR = Path("output")


def load_config():
    load_dotenv()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print("Error: ANTHROPIC_API_KEY must be set in .env.", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Download clips, generate captions, and burn them into the video."
    )
    parser.add_argument(
        "--input", default="clips.json", help="Path to clips JSON file (default: clips.json)"
    )
    parser.add_argument(
        "--count", type=int, default=3, help="Number of top clips to prepare (default: 3)"
    )
    args = parser.parse_args()
    return anthropic_key, args.input, args.count


def get_top_clips(input_path: str, count: int) -> tuple[str, list[dict]]:
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: '{input_path}' not found. Run main.py or main_kick.py first.", file=sys.stderr)
        sys.exit(1)

    clips = data.get("clips", [])
    if not clips:
        print("No clips found in the input file.", file=sys.stderr)
        sys.exit(1)

    streamer = data.get("streamer_login", "streamer")
    return streamer, clips[:count]


def generate_caption(clip: dict, streamer: str, anthropic_key: str) -> str:
    client = anthropic.Anthropic(api_key=anthropic_key)
    prompt = (
        f"Write a 3-5 word caption summarizing this gaming clip using Gen Z slang.\n"
        f"Streamer: {streamer}\n"
        f"Clip title: {clip['title']}\n"
        f"Rules:\n"
        f"- Maximum 5 words\n"
        f"- Use Gen Z language (e.g. no cap, bussin, slay, cooked, ate, lowkey, fr fr, W, L, rizz, caught in 4k, it's giving, NPC)\n"
        f"- No hashtags, no punctuation\n"
        f"- Output ONLY the caption words, nothing else"
    )
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def download_clip(clip_url: str, clip_id: str) -> Path:
    raw_path = Path(f"/tmp/clip_raw_{clip_id}.mp4")
    if raw_path.exists():
        print(f"  Already downloaded: {raw_path}")
        return raw_path

    print(f"  Downloading...")
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(raw_path).replace(".mp4", ".%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([clip_url])

    candidates = list(Path("/tmp").glob(f"clip_raw_{clip_id}.*"))
    if not candidates:
        raise RuntimeError("Download completed but file not found in /tmp.")
    actual = candidates[0]
    if actual != raw_path:
        actual.rename(raw_path)

    print(f"  Downloaded ({raw_path.stat().st_size / 1e6:.1f} MB)")
    return raw_path


def burn_caption(input_path: Path, caption: str, output_path: Path):
    # Escape special characters for FFmpeg drawtext
    safe_caption = (
        caption
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
    )

    drawtext = (
        f"drawtext="
        f"text='{safe_caption}':"
        f"fontcolor=white:"
        f"fontsize=56:"
        f"font='Arial Bold':"
        f"box=1:"
        f"boxcolor=black@0.55:"
        f"boxborderw=16:"
        f"x=(w-text_w)/2:"
        f"y=h-text_h-60"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", drawtext,
        "-c:a", "copy",
        "-preset", "fast",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-500:]}")


def main():
    anthropic_key, input_path, count = load_config()

    streamer, clips = get_top_clips(input_path, count)
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Preparing {len(clips)} clip(s) for '{streamer}'...\n")

    for i, clip in enumerate(clips, 1):
        print(f"[{i}/{len(clips)}] \"{clip['title']}\" — {clip['view_count']:,} views")

        caption = generate_caption(clip, streamer, anthropic_key)
        print(f"  Caption: {caption}")

        raw_video = download_clip(clip["url"], clip["clip_id"])

        output_filename = f"{streamer}_{clip['clip_id']}.mp4"
        output_path = OUTPUT_DIR / output_filename

        print(f"  Burning caption...")
        burn_caption(raw_video, caption, output_path)

        print(f"  Saved → {output_path}\n")

    print(f"Done. {len(clips)} clip(s) ready in ./{OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
