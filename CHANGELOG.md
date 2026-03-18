# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

## [0.3.0] - 2026-03-17
### Added
- `prepare_clips.py`: local clip preparation pipeline — downloads clips and burns Gen Z-style captions into video files using FFmpeg
- Kick platform support via `main_kick.py`, using the public Kick API (no credentials required)
- 7-day date filtering for Kick clips
- `cloudscraper` dependency for bypassing Cloudflare on the Kick API

## [0.2.0] - 2026-03-10
### Added
- `post_to_tiktok.py`: full OAuth2 + PKCE authentication flow for TikTok
- AI caption generation using Claude Haiku (Anthropic API)
- Chunked video upload to TikTok Content Posting API
- Token caching to avoid repeated browser authorization
- `yt-dlp` integration for downloading clips prior to upload

## [0.1.0] - 2026-03-02
### Added
- Initial project structure
- `main.py`: fetch top clips from Twitch Helix API over the last 7 days
- Clips ranked by view count and written to `clips.json`
- `.env`-based credential management via `python-dotenv`
- `.gitignore` for secrets and virtual environments
