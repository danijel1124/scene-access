"""Downloads a video from YouTube (or another site yt-dlp supports) in a quality fit for the model."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

DEFAULT_MAX_HEIGHT = 480
MAX_HEIGHT_CHOICES = (240, 360, 480, 720, 1080)

# yt-dlp output template fields usable for a normal download, in the order they appear in the
# file name. Left out: ext (always appended), url and description (can be thousands of characters),
# and fields that only exist with --print, after the download, or with section/SponsorBlock options.
FILENAME_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "Video title"),
    ("id", "Video identifier"),
    ("fulltitle", "Video title ignoring live timestamp and generic title"),
    ("alt_title", "Secondary title of the video"),
    ("display_id", "Alternative identifier for the video"),
    ("uploader", "Full name of the video uploader"),
    ("uploader_id", "Nickname or ID of the video uploader"),
    ("uploader_url", "URL of the uploader's profile"),
    ("license", "License the video is published under"),
    ("creators", "Creators of the video"),
    ("creator", "Creators of the video, comma-separated"),
    ("timestamp", "UNIX timestamp when the video became available"),
    ("upload_date", "Upload date (YYYYMMDD, UTC)"),
    ("release_timestamp", "UNIX timestamp when the video was released"),
    ("release_date", "Release date (YYYYMMDD, UTC)"),
    ("release_year", "Release year (YYYY)"),
    ("modified_timestamp", "UNIX timestamp of the last modification"),
    ("modified_date", "Date of the last modification (YYYYMMDD, UTC)"),
    ("channel", "Name of the channel"),
    ("channel_id", "ID of the channel"),
    ("channel_url", "URL of the channel"),
    ("channel_follower_count", "Number of channel followers"),
    ("channel_is_verified", "Whether the channel is verified"),
    ("location", "Where the video was filmed"),
    ("duration", "Length in seconds"),
    ("duration_string", "Length (HH:mm:ss)"),
    ("view_count", "Number of views"),
    ("concurrent_view_count", "Number of current viewers"),
    ("like_count", "Number of positive ratings"),
    ("dislike_count", "Number of negative ratings"),
    ("repost_count", "Number of reposts"),
    ("average_rating", "Average user rating"),
    ("comment_count", "Number of comments"),
    ("save_count", "Number of saves or bookmarks"),
    ("age_limit", "Age restriction in years"),
    ("live_status", "Live status, for example not_live or was_live"),
    ("is_live", "Whether it is a live stream"),
    ("was_live", "Whether it was originally a live stream"),
    ("playable_in_embed", "Whether it may be embedded on other sites"),
    ("availability", "Availability, for example public or unlisted"),
    ("media_type", "Type of media, for example episode or clip"),
    ("start_time", "Start time in seconds given in the URL"),
    ("end_time", "End time in seconds given in the URL"),
    ("extractor", "Name of the extractor"),
    ("extractor_key", "Key name of the extractor"),
    ("epoch", "UNIX time when the information was extracted"),
    ("autonumber", "Number increased with each download"),
    ("video_autonumber", "Number increased with each video"),
    ("n_entries", "Total number of items in the playlist"),
    ("playlist_id", "ID of the playlist"),
    ("playlist_title", "Name of the playlist"),
    ("playlist", "Playlist title, or its ID"),
    ("playlist_count", "Number of items in the playlist"),
    ("playlist_index", "Position of the video in the playlist"),
    ("playlist_autonumber", "Position in the playlist download queue"),
    ("playlist_uploader", "Name of the playlist uploader"),
    ("playlist_uploader_id", "ID of the playlist uploader"),
    ("playlist_channel", "Name of the channel that uploaded the playlist"),
    ("playlist_channel_id", "ID of the channel that uploaded the playlist"),
    ("playlist_webpage_url", "URL of the playlist page"),
    ("webpage_url", "URL of the video page"),
    ("webpage_url_basename", "Last part of the video page URL"),
    ("webpage_url_domain", "Domain of the video page URL"),
    ("original_url", "URL as entered"),
    ("categories", "Categories of the video"),
    ("tags", "Tags of the video"),
    ("cast", "Cast members"),
    ("chapter", "Chapter title"),
    ("chapter_number", "Chapter number"),
    ("chapter_id", "Chapter ID"),
    ("series", "Title of the series or program"),
    ("series_id", "ID of the series or program"),
    ("season", "Title of the season"),
    ("season_number", "Season number"),
    ("season_id", "ID of the season"),
    ("episode", "Title of the episode"),
    ("episode_number", "Episode number within the season"),
    ("episode_id", "ID of the episode"),
    ("track", "Title of the track"),
    ("track_number", "Track number"),
    ("track_id", "ID of the track"),
    ("artists", "Artists of the track"),
    ("artist", "Artists of the track, comma-separated"),
    ("genres", "Genres"),
    ("genre", "Genres, comma-separated"),
    ("composers", "Composers"),
    ("composer", "Composers, comma-separated"),
    ("album", "Title of the album"),
    ("album_type", "Type of the album"),
    ("album_artists", "All artists on the album"),
    ("album_artist", "All album artists, comma-separated"),
    ("disc_number", "Disc number"),
    ("format", "Human-readable description of the format"),
    ("format_id", "Short ID of the format"),
    ("format_note", "Additional format information"),
    ("resolution", "Width and height as text"),
    ("width", "Video width"),
    ("height", "Video height"),
    ("aspect_ratio", "Aspect ratio"),
    ("fps", "Frame rate"),
    ("dynamic_range", "Dynamic range"),
    ("vcodec", "Video codec"),
    ("acodec", "Audio codec"),
    ("container", "Container format"),
    ("tbr", "Average total bitrate (kbps)"),
    ("vbr", "Average video bitrate (kbps)"),
    ("abr", "Average audio bitrate (kbps)"),
    ("asr", "Audio sampling rate (Hz)"),
    ("audio_channels", "Number of audio channels"),
    ("stretched_ratio", "Pixel aspect ratio, if not square"),
    ("filesize", "File size in bytes"),
    ("filesize_approx", "Estimated file size in bytes"),
    ("protocol", "Download protocol"),
    ("language", "Language code"),
)
FILENAME_FIELD_NAMES = tuple(name for name, _description in FILENAME_FIELDS)
# yt-dlp's own default: "%(title)s [%(id)s].%(ext)s".
DEFAULT_FILENAME_FIELDS = ("title", "id")


def output_template(fields: tuple[str, ...] | list[str]) -> str:
    """Builds yt-dlp's default naming scheme for any fields: the first plain, the rest in brackets."""
    chosen = [name for name in FILENAME_FIELD_NAMES if name in fields] or list(DEFAULT_FILENAME_FIELDS)
    parts = [f"%({name})s" for name in chosen]
    return parts[0] + "".join(f" [{part}]" for part in parts[1:]) + ".%(ext)s"


class _QuietLogger:
    # A windowed app has no console; yt-dlp must not print. Errors still raise DownloadError.
    def debug(self, _message: str) -> None:
        pass

    def info(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, _message: str) -> None:
        pass


def download_options(target_dir: Path, max_height: int, progress: Callable[[dict[str, Any]], None] | None = None,
                     filename_fields: tuple[str, ...] | list[str] = DEFAULT_FILENAME_FIELDS) -> dict[str, Any]:
    options: dict[str, Any] = {
        # Best resolution up to max_height, preferring H.264 in mp4 with m4a audio (most widely accepted
        # by models; YouTube otherwise often picks AV1), then the smallest file. Video and audio are
        # merged, so the model also gets the sound.
        "format_sort": [f"res:{max_height}", "vcodec:h264", "ext:mp4:m4a", "+size"],
        "merge_output_format": "mp4",
        "noplaylist": True,
        "outtmpl": str(target_dir / output_template(filename_fields)),
        # Many fields together can exceed the Windows file name limit.
        "trim_file_name": 180,
        "logger": _QuietLogger(),
        "quiet": True,
        "noprogress": True,
        # yt-dlp enables only Deno by default; Node is used too when installed. Without either,
        # YouTube still works with fewer formats.
        "js_runtimes": {"deno": {}, "node": {}},
    }
    try:
        import imageio_ffmpeg
        options["ffmpeg_location"] = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass  # Fall back to an ffmpeg on PATH.
    if progress:
        options["progress_hooks"] = [progress]
    return options


def download_video(url: str, target_dir: Path, max_height: int = DEFAULT_MAX_HEIGHT,
                   progress: Callable[[dict[str, Any]], None] | None = None,
                   filename_fields: tuple[str, ...] | list[str] = DEFAULT_FILENAME_FIELDS) -> tuple[Path, str]:
    import yt_dlp

    target_dir.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(download_options(target_dir, max_height, progress, filename_fields)) as downloader:
        info = downloader.extract_info(url, download=True)
    path = Path(info["requested_downloads"][0]["filepath"])
    return path, str(info.get("title") or path.name)
