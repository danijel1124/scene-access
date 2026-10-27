"""Match playlist entries to completed local videos, never to partial downloads."""
from __future__ import annotations

import re
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')

_JOB_PREFIX = re.compile(r'^\[[a-f0-9]{32}\] ')


def video_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    identifier = ''
    if host in ('youtu.be', 'www.youtu.be'):
        identifier = parsed.path.strip('/').split('/')[0]
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        if parsed.path == '/watch':
            identifier = parse_qs(parsed.query).get('v', [''])[0]
        elif parsed.path.startswith(('/shorts/', '/embed/', '/live/')):
            identifier = parsed.path.split('/')[2]
    return 'youtube:' + identifier if _VIDEO_ID.fullmatch(identifier) else url


def _completed(path: Path, folder: Path) -> bool:
    try:
        info = path.lstat()
        return (path.parent.resolve() == folder and stat.S_ISREG(info.st_mode)
                and info.st_size > 0 and path.suffix.lower() in ('.mp4', '.webm', '.mov'))
    except OSError:
        return False


def playlist_options(entries: list[dict], folder: Path, chats: list[dict], *, hide_existing_chats: bool = True) -> list[dict]:
    """Existing file+chat: hidden. Existing orphan: reusable. Missing file: downloadable."""
    folder = folder.resolve()
    by_key: dict[str, Path] = {}
    by_name: dict[str, list[Path]] = {}
    chat_paths = {Path(row['source_path']).resolve() for row in chats}
    claims: dict[Path, set[str]] = {}
    for row in chats:
        if row.get('source_url'):
            claims.setdefault(Path(row['source_path']).resolve(), set()).add(video_key(row['source_url']))
    # Saved identities take precedence over all filename comparisons.
    for row in chats:
        path = Path(row['source_path'])
        if row.get('source_url') and _completed(path, folder):
            key = video_key(row['source_url'])
            if claims.get(path.resolve()) == {key}:
                by_key[key] = path
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if _completed(path, folder):
                name = _JOB_PREFIX.sub('', path.name)
                by_name.setdefault(name, []).append(path)
    expected: dict[str, set[str]] = {}
    for entry in entries:
        for name in entry.get('filenames') or [entry.get('filename', '')]:
            if not name:
                continue
            expected.setdefault(name, set()).add(video_key(entry['url']))
    result, seen = [], set()
    for entry in entries:
        key = video_key(entry['url'])
        if key in seen:
            continue
        seen.add(key)
        path = by_key.get(key)
        if path is None:
            for name in entry.get('filenames') or [entry.get('filename', '')]:
                if not name:
                    continue
                for candidate in by_name.get(name, []):
                    known = claims.get(candidate.resolve())
                    if known == {key} or (not known and expected[name] == {key}):
                        path = candidate
                        break
                if path is not None:
                    break
        if hide_existing_chats and path is not None and path.resolve() in chat_paths:
            continue
        result.append({**entry, 'local_path': path})
    return result
