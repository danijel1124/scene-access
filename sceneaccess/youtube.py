"""Downloads a video from YouTube (or another site yt-dlp supports) in a quality fit for the model."""

from __future__ import annotations

from pathlib import Path
import os
import stat
import uuid
from typing import Any, Callable

from .request_control import RequestControl
from .worker_process import run_isolated_task


def _single_video_downloader_class() -> Any:
    """Reject collections before yt-dlp visits children or starts media transfer."""
    import yt_dlp

    class SingleVideoDownloader(yt_dlp.YoutubeDL):
        def process_ie_result(self, ie_result, download=True, extra_info=None):
            # noplaylist disambiguates watch URLs but does not block pure collections.
            if ie_result.get('_type') in ('playlist', 'multi_video', 'compat_list'):
                raise ValueError('Please select a single video before using this collection.')
            return super().process_ie_result(ie_result, download=download, extra_info=extra_info)

    return SingleVideoDownloader


class DownloadFiles:
    """Exact job paths, admitted only before creation; never infer ownership from a folder diff.

    Existing files (including resumed .part files) are never owned. Identity checks also spare
    replacements observed after a progress hook. Unknown sidecars are deliberately left alone.
    """

    def __init__(self, target_dir: Path, on_change: Callable[[dict], None] | None = None):
        self.target_dir = target_dir.resolve()
        self._paths: dict[Path, tuple[int, int] | None] = {}
        self._protected: set[Path] = set()
        self._protected_ids: set[tuple[int, int]] = set()
        self._on_change = on_change

    def snapshot(self) -> dict:
        return {"paths": {str(path): identity for path, identity in self._paths.items()},
                "protected": [str(path) for path in self._protected],
                "protected_ids": list(self._protected_ids)}

    def adopt(self, state: dict) -> None:
        """Import the worker's pre-creation decisions, never infer them from current files."""
        for path in state['protected']:
            self._protected.add(self.path(path))
        self._protected_ids.update(tuple(identity) for identity in state['protected_ids'])
        for path, identity in state['paths'].items():
            path = self.path(path)
            if path not in self._protected:
                self._paths[path] = tuple(identity) if identity is not None else None

    def _notify(self) -> None:
        if self._on_change:
            self._on_change(self.snapshot())

    def path(self, value: str | Path) -> Path:
        path = Path(value).absolute()
        if path.parent.resolve() != self.target_dir or path.is_symlink():
            raise ValueError(f"Download path is outside the target folder or a symlink: {path}")
        return path

    def reserve(self, value: str | Path) -> None:
        path = self.path(value)
        if path in self._paths or path in self._protected:
            return
        if path.exists():
            self._protected.add(path)
            info = path.lstat()
            self._protected_ids.add((info.st_dev, info.st_ino))
        else:
            self._paths[path] = None
        self._notify()

    def observe(self) -> None:
        for path, identity in self._paths.items():
            if identity is not None:
                continue
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode):
                    self._paths[path] = (info.st_dev, info.st_ino)
            except OSError:
                pass
        self._notify()

    def cleanup(self) -> None:
        for path, identity in self._paths.items():
            if identity is None or identity in self._protected_ids:
                continue
            try:
                self.path(path)
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
                    path.unlink()
            except (OSError, ValueError):
                pass

    def claim_merge_output(self, final: Path) -> tuple[Path, tuple[int, int]]:
        """Own the merge inode before ffmpeg writes, and track its eventual rename."""
        temporary = final.with_name(final.stem + '.temp' + final.suffix)
        self.reserve(final)
        self.reserve(temporary)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
        finally:
            os.close(descriptor)
        self._paths[temporary] = identity
        if final in self._paths and self._paths[final] is None:
            self._paths[final] = identity
        self._notify()
        return temporary, identity

    def release_empty_placeholder(self, placeholder: tuple[Path, tuple[int, int]]) -> None:
        path, identity = placeholder
        try:
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_size == 0 and (info.st_dev, info.st_ino) == identity:
                path.unlink()
        except FileNotFoundError:
            pass


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


# Browsers whose cookies yt-dlp can read on Windows. Chrome and Edge encrypt them with app-bound keys,
# which fails with "Failed to decrypt with DPAPI" (yt-dlp issue 10927); a cookies.txt file works for them.
COOKIE_BROWSERS = ("firefox",)


def cookie_options(cookies: str) -> dict[str, Any]:
    """yt-dlp options for YouTube cookies: "" for none, a browser name, or the path of a cookies.txt file.
    Signed in, YouTube asks less often to "confirm you're not a bot"."""
    if not cookies:
        return {}
    if cookies in COOKIE_BROWSERS:
        return {"cookiesfrombrowser": (cookies,)}
    return {"cookiefile": cookies}


def needs_sign_in(error: BaseException) -> bool:
    """True for YouTube's "Sign in to confirm you're not a bot", which cookies of a signed-in browser avoid."""
    text = str(error)
    return "Sign in to confirm" in text or "--cookies" in text


def download_options(target_dir: Path, max_height: int, progress: Callable[[dict[str, Any]], None] | None = None,
                     filename_fields: tuple[str, ...] | list[str] = DEFAULT_FILENAME_FIELDS,
                     cookies: str = "") -> dict[str, Any]:
    options: dict[str, Any] = {
        **cookie_options(cookies),
        # Best resolution up to max_height, preferring H.264 in mp4 with m4a audio (most widely accepted
        # by models; YouTube otherwise often picks AV1), then the smallest file. Video and audio are
        # merged, so the model also gets the sound.
        "format_sort": [f"res:{max_height}", "vcodec:h264", "ext:mp4:m4a", "+size"],
        "merge_output_format": "mp4",
        "noplaylist": True,
        "outtmpl": output_template(filename_fields),
        "paths": {"home": str(target_dir)},
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


def _download_video_isolated_worker(url: str, target_dir: Path, max_height: int, filename_fields,
                                   cookies: str, _progress_pipe=None, _job_id: str = "") -> tuple[Path, str, dict[str, Any]]:
    def state_changed(state: dict) -> None:
        if _progress_pipe is not None:
            _progress_pipe.send(('event', state))

    def progress(update: dict[str, Any]) -> None:
        if _progress_pipe is not None:
            # Do not pickle the whole yt-dlp info_dict into every progress event.
            fields = ('status', 'downloaded_bytes', 'total_bytes', 'total_bytes_estimate')
            _progress_pipe.send(('progress', {key: update[key] for key in fields if key in update}))

    files = DownloadFiles(target_dir, on_change=state_changed)
    return download_video(url, target_dir, max_height, progress, filename_fields, cookies,
                          files=files, _job_id=_job_id)


def download_video(url: str, target_dir: Path, max_height: int = DEFAULT_MAX_HEIGHT,
                   progress: Callable[[dict[str, Any]], None] | None = None,
                   filename_fields: tuple[str, ...] | list[str] = DEFAULT_FILENAME_FIELDS,
                   cookies: str = "", *, files: DownloadFiles | None = None,
                   _job_id: str = "") -> tuple[Path, str, dict[str, Any]]:
    import yt_dlp
    target_dir.mkdir(parents=True, exist_ok=True)
    files = files if files is not None else DownloadFiles(target_dir)
    def tracked_progress(update: dict[str, Any]) -> None:
        files.observe()
        if progress:
            progress(update)
    class JobDownloader(_single_video_downloader_class()):
        def dl(self, name, info, subtitle=False, test=False):
            if not test:
                for candidate in (name, name + ".part", name + ".ytdl", name + ".part.ytdl"):
                    files.reserve(candidate)
            try:
                return super().dl(name, info, subtitle=subtitle, test=test)
            finally:
                files.observe()
        def run_pp(self, pp, infodict):
            placeholder = None
            if infodict.get("filepath"):
                final = files.path(infodict["filepath"])
                placeholder = files.claim_merge_output(final)
            try:
                return super().run_pp(pp, infodict)
            finally:
                files.observe()
                if placeholder:
                    files.release_empty_placeholder(placeholder)
    try:
        options = download_options(target_dir, max_height, tracked_progress, filename_fields, cookies)
        if _job_id:
            # yt-dlp trims from the end. Keep the complete job identifier at the
            # beginning, outside the user-controlled long metadata fields.
            template = f'[{_job_id}] ' + output_template(filename_fields)
            options['outtmpl'] = template
        with JobDownloader(options) as downloader:
            info = downloader.extract_info(url, download=True)
        path = files.path(info["requested_downloads"][0]["filepath"])
        return path, str(info.get("title") or path.name), video_info_summary(info)
    except BaseException:
        files.cleanup()
        raise


def download_video_isolated(
    url: str,
    target_dir: Path,
    max_height: int = 480,
    progress: Callable[[dict[str, Any]], None] | None = None,
    filename_fields: tuple[str, ...] | list[str] = DEFAULT_FILENAME_FIELDS,
    cookies: str = "",
    *,
    control: RequestControl | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Isolated download in a separate process; never reuses an existing file of the same job."""
    if control is None:
        control = RequestControl()

    args = (url, target_dir, max_height, filename_fields, cookies)
    files = DownloadFiles(target_dir)
    try:
        return run_isolated_task(_download_video_isolated_worker, args, control=control,
                                 kwargs={'_job_id': uuid.uuid4().hex}, progress_callback=progress,
                                 event_callback=files.adopt, timeout=86400)
    except BaseException:
        files.cleanup()
        raise


# Information about a video the model can ask for; everything else in yt-dlp's info is format detail.
VIDEO_INFO_FIELDS = ("title", "channel", "uploader", "upload_date", "duration_string", "webpage_url", "description",
                     "categories", "tags", "chapters", "view_count", "like_count", "language")


def video_info_summary(info: dict[str, Any]) -> dict[str, Any]:
    summary = {name: info[name] for name in VIDEO_INFO_FIELDS if info.get(name) not in (None, "", [])}
    if "chapters" in summary:
        summary["chapters"] = [{key: chapter.get(key) for key in ("start_time", "end_time", "title")}
                               for chapter in summary["chapters"]]
    return summary


def _info_options(cookies: str = "") -> dict[str, Any]:
    # ignore_no_formats_error: only the information is needed, so formats that cannot be resolved don't matter.
    return {"logger": _QuietLogger(), "quiet": True, "noplaylist": True, "skip_download": True,
            "ignore_no_formats_error": True, "js_runtimes": {"deno": {}, "node": {}}, **cookie_options(cookies)}


def search_videos(query: str, count: int = 8, cookies: str = "") -> list[dict[str, Any]]:
    """YouTube search results with url, title, channel and duration (seconds), without downloading."""
    import yt_dlp

    with yt_dlp.YoutubeDL({**_info_options(cookies), "extract_flat": "in_playlist"}) as downloader:
        info = downloader.extract_info(f"ytsearch{count}:{query}", download=False)
    results = []
    for entry in info.get("entries") or []:
        if entry and entry.get("id"):
            results.append({"url": f"https://www.youtube.com/watch?v={entry['id']}", "title": entry.get("title") or "",
                            "channel": entry.get("channel") or entry.get("uploader") or "",
                            "duration": entry.get("duration")})
    return results


def list_video_choices(url: str, cookies: str = "",
                       filename_fields: tuple[str, ...] | list[str] = DEFAULT_FILENAME_FIELDS,
                       max_height: int = DEFAULT_MAX_HEIGHT) -> dict[str, Any]:
    """Picklable metadata for a single video or flat collection, never media downloads.

    Consume lazy entries while the downloader (and its cookie/network context) is open.
    Nested collections are omitted rather than recursively expanded. The caller chooses
    one returned URL and runs the usual single-video download separately. ``filename``
    predicts the normal basename via yt-dlp's naming/trim rules, without a job prefix.
    Flat entries lack some metadata: unknown extensions offer mp4, webm and mov
    candidates without another extraction. Fields not supplied by the extractor
    retain yt-dlp's normal NA fallback.
    Run-dependent templates return no filename candidates: only an existing
    saved source identity can associate those files, never a downloader-local counter.
    """
    import yt_dlp
    from yt_dlp.utils import DownloadError
    from urllib.parse import urlsplit

    class ChoiceDownloader(yt_dlp.YoutubeDL):
        def process_ie_result(self, ie_result, download=True, extra_info=None):
            if extra_info and 'playlist' in extra_info:
                kind = ie_result.get('_type')
                if kind in ('playlist', 'multi_video', 'compat_list'):
                    return ie_result  # Leave nested collections opaque; never walk their entries.
                if kind in ('url', 'url_transparent') and not ie_result.get('url'):
                    return None
                if ie_result.get('availability') in ('private', 'premium_only', 'subscriber_only', 'needs_auth'):
                    return None
            return super().process_ie_result(ie_result, download=download, extra_info=extra_info)

    options = {**download_options(Path('.'), max_height, filename_fields=filename_fields, cookies=cookies),
               **_info_options(cookies), "extract_flat": "in_playlist"}
    prefix = '[' + '0' * 32 + '] '
    naming_options = {'outtmpl': prefix + output_template(filename_fields),
                      'trim_file_name': options['trim_file_name'], 'quiet': True, 'logger': _QuietLogger()}
    with ChoiceDownloader(options) as downloader, yt_dlp.YoutubeDL(naming_options) as prefixed_names:
        info = downloader.extract_info(url, download=False)
        if not info:
            return {"is_playlist": False, "title": "", "entries": []}
        is_playlist = info.get('_type') in ('playlist', 'multi_video', 'compat_list')
        entries = (info.get('entries') or []) if is_playlist else [info]
        choices = []
        for entry in entries:
            if not entry or entry.get('_type') in ('playlist', 'multi_video', 'compat_list'):
                continue
            # Generic multi-video pages give every child the *collection* webpage_url.
            # Their media URL is the only individually selectable URL in that case.
            entry_url = entry.get('url')
            webpage = entry.get('webpage_url')
            if webpage and (not is_playlist or webpage != info.get('webpage_url')):
                entry_url = webpage
            if not isinstance(entry_url, str) or urlsplit(entry_url).scheme not in ('http', 'https'):
                continue
            identifier = str(entry.get('id') or entry_url)
            title = str(entry.get('title') or identifier)
            # A generic collection's synthetic title/id is not used when the
            # selected media URL is extracted on its own. Resolve that metadata
            # with downloads disabled so prediction uses the same context.
            standalone = entry
            fields = filename_fields or DEFAULT_FILENAME_FIELDS
            needs_standalone = (webpage == info.get('webpage_url') and webpage != entry_url
                                or any(field not in entry or field.startswith('playlist') for field in fields))
            if is_playlist and needs_standalone:
                try:
                    standalone = downloader.extract_info(entry_url, download=False)
                except DownloadError:
                    # One unavailable child's metadata must not discard the other
                    # choices. Root extraction and cancellation still propagate.
                    continue
                if not standalone or standalone.get('_type') in ('playlist', 'multi_video', 'compat_list'):
                    continue
            known_extension = standalone.get('ext')
            extensions = (known_extension,) if known_extension else ('mp4', 'webm', 'mov')
            # These fields belong to a downloader instance, not to the video.
            # A fresh single-video download and a playlist lookup have different
            # counter states; epoch is the extraction time, not a video property.
            # Never advertise these guesses as file identities.
            if {'autonumber', 'video_autonumber', 'epoch'}.intersection(fields):
                extensions = ()
            filenames = []
            for extension in extensions:
                naming_info = {**standalone, 'ext': extension}
                filenames.append(Path(downloader.prepare_filename(naming_info)).name)
                prefixed = Path(prefixed_names.prepare_filename(naming_info)).name
                filenames.append(prefixed.removeprefix(prefix))
            filenames = list(dict.fromkeys(filenames))
            filename = filenames[0] if filenames else ''
            choices.append({"id": identifier, "url": entry_url, "title": title, "filename": filename,
                            "filenames": filenames,
                            "info": downloader.sanitize_info(video_info_summary(standalone))})
        return {"is_playlist": is_playlist, "title": str(info.get('title') or ""), "entries": choices}


def fetch_video_info(url: str, cookies: str = "") -> dict[str, Any]:
    """The video information the model may read, for a video that is not downloaded again."""
    downloader_type = _single_video_downloader_class()
    with downloader_type(_info_options(cookies)) as downloader:
        return video_info_summary(downloader.extract_info(url, download=False))


def download_section(url: str, start: float, end: float, target_dir: Path, max_height: int = 2160,
                     cookies: str = "") -> Path:
    """Downloads only the video picture between start and end seconds, in high resolution."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    options = download_options(target_dir, max_height, cookies=cookies)
    options.update({
        # Picture only: the clip is for still frames. Plain https formats let ffmpeg seek to the range; with
        # HLS (m3u8) it reads the whole playlist and wrote an empty file in testing.
        "format": "bv*[protocol=https]/b[protocol=https]/bv*/b",
        "outtmpl": str(target_dir / "section.%(ext)s"),
        "download_ranges": download_range_func(None, [(max(start, 0.0), end)]),
        # Cut exactly at start, so a time inside the clip matches the same time in the video.
        "force_keyframes_at_cuts": True,
    })
    options.pop("merge_output_format", None)
    downloader_type = _single_video_downloader_class()
    with downloader_type(options) as downloader:
        info = downloader.extract_info(url, download=True)
    return Path(info["requested_downloads"][0]["filepath"])
