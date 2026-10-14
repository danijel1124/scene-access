"""Starting a video chat: from a local file or YouTube, with the start dialog, the video size question,
shrinking, the download and the YouTube sign-in. Mixed into the main window (gui.MainFrame)."""

from __future__ import annotations

import shutil
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import wx

from .config import save_config
from .dialogs import (NO_PROFILE, SIZE_BACK, SIZE_LOWER, SIZE_SEND, SIZE_SHRINK, SizeDialog, StartChatDialog,
                      ask_youtube_sign_in, cookies_description, default_download_dir)
from .models import AUTO_ROUTER
from .prompts import system_prompt
from .session_store import TRANSCRIPT_PENDING
from .speech import announce
from .tools import tool_definitions
from .video_size import (SIZE_LIMIT, ShrinkCancelled, base64_size, megabytes, next_lower_height, shrink_video,
                         video_height)
from .youtube import MAX_HEIGHT_CHOICES, download_video, needs_sign_in


class DownloadCancelled(Exception):
    """Raised from the progress hook to stop a download the user cancelled."""


def url_from_clipboard() -> str:
    """Suggests a copied link as the default, so it does not have to be pasted."""
    text = ""
    if wx.TheClipboard.Open():
        try:
            data = wx.TextDataObject()
            if wx.TheClipboard.GetData(data):
                text = data.GetText().strip()
        finally:
            wx.TheClipboard.Close()
    return text if text.startswith(("https://", "http://")) and len(text.split()) == 1 else ""


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class NewChatMixin:
    """The New video chat part of the main window. Uses the window's config, store, owner_id, status,
    new_button, refresh_sessions, render_current and _send."""

    def on_new(self, _event: wx.CommandEvent) -> None:
        menu = wx.Menu()
        # Read the IDs now: after menu.Destroy() the items are freed and GetId() returns garbage.
        local = menu.Append(wx.ID_ANY, "Start chat from local file").GetId()
        youtube = menu.Append(wx.ID_ANY, "Start chat from YouTube").GetId()
        # Returns the chosen ID once the menu has closed, so the next dialog does not open from inside it.
        chosen = self.new_button.GetPopupMenuSelectionFromUser(menu, (0, self.new_button.GetSize().height))
        menu.Destroy()
        if chosen == local:
            self.new_from_local_file()
        elif chosen == youtube:
            self.new_from_youtube()

    def new_from_local_file(self) -> None:
        with wx.FileDialog(self, "Choose a local video", wildcard="Video (*.mp4;*.webm;*.mov)|*.mp4;*.webm;*.mov", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            source = Path(dialog.GetPath())
        start = None
        while True:
            start = self._ask_first_question(online=False, previous=start)
            if not start:
                return
            sent = self._video_to_send(source, online=False)
            if sent != SIZE_BACK:
                break
        if isinstance(sent, Path):
            self._start_chat(source, source.name, *start, sent_video=sent)

    def new_from_youtube(self) -> None:
        with wx.TextEntryDialog(self, "YouTube link:", "Start chat from YouTube", url_from_clipboard()) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            url = dialog.GetValue().strip()
        if not url:
            return
        start, downloaded, downloaded_height, height = None, None, 0, 0
        while True:
            asked_height = self.config.youtube_max_height
            start = self._ask_first_question(online=True, previous=start)
            if not start:
                return
            target_dir = self._download_dir()
            if target_dir is None:
                return
            if not height or self.config.youtube_max_height != asked_height:
                # The first round, or the quality was changed in Advanced; otherwise a file already
                # stepped down stays, e.g. when the user only came back to turn on automatic shrinking.
                height = self.config.youtube_max_height
            lowering = False
            while True:
                if downloaded is None or downloaded_height != height:
                    if downloaded:
                        # The program downloaded this file itself; a new quality replaces it under the same name.
                        _remove_file(downloaded[0])
                    downloaded = self._download_with_progress(url, target_dir, height)
                    if not downloaded:
                        return
                    downloaded_height = height
                lower = next_lower_height(height, video_height(downloaded[0]), MAX_HEIGHT_CHOICES)
                if lowering and lower and base64_size(downloaded[0].stat().st_size) > SIZE_LIMIT:
                    # Still too large: the next lower quality, until the video fits.
                    height = lower
                    continue
                sent = self._video_to_send(downloaded[0], online=True, lower_height=lower)
                if sent != SIZE_LOWER:
                    break
                height, lowering = lower, True
            if sent != SIZE_BACK:
                break
        if isinstance(sent, Path):
            path, title, info = downloaded
            self._start_chat(path, title, *start, source_url=url, video_info=info, sent_video=sent)

    def _video_to_send(self, source: Path, online: bool, lower_height: int | None = None) -> Path | int | None:
        """The file to send for a new chat: the video itself or a shrunk copy in a temporary folder. Returns
        SIZE_BACK or SIZE_LOWER if the user chose that, or None if cancelled. lower_height is the next lower
        quality of an online video, None if there is none."""
        size = source.stat().st_size
        if self.config.auto_shrink:
            return self._shrink_with_progress(source) if base64_size(size) > self._shrink_target() else source
        model = self.config.model
        if (base64_size(size) <= SIZE_LIMIT or not self.config.check_video_size
                or (model != AUTO_ROUTER and model in self.config.size_check_skipped_models)):
            return source
        with SizeDialog(self, size, model, online, lower_height is not None) as dialog:
            choice = dialog.ShowModal()
            dont_ask = dialog.dont_ask.IsShown() and dialog.dont_ask.GetValue()
        if choice == SIZE_SHRINK:
            return self._shrink_with_progress(source)
        if choice == SIZE_SEND:
            # Remembered only once the first answer arrives, i.e. sending this size really worked.
            self.pending_size_skip = model if dont_ask else ""
            return source
        return choice if choice in (SIZE_BACK, SIZE_LOWER) else None

    def _shrink_target(self) -> int:
        return self.config.shrink_target_mb * 1_000_000

    def _shrink_with_progress(self, source: Path) -> Path | None:
        """Shrinks the video into a temporary folder; _start_chat removes it. None if cancelled or failed."""
        folder = Path(tempfile.mkdtemp(prefix="sceneaccess-"))
        target = folder / (source.stem + ".mp4")
        state: dict[str, Any] = {"fraction": 0.0, "done": False, "cancel": False, "error": None}

        def worker() -> None:
            try:
                shrink_video(source, target, self._shrink_target(), lambda fraction: state.update(fraction=fraction),
                             lambda: state["cancel"])
            except Exception as error:
                state["error"] = error
            finally:
                state["done"] = True

        announce("Shrinking the video…")
        threading.Thread(target=worker, daemon=True).start()
        message = f"Shrinking the video with ffmpeg to at most {megabytes(self._shrink_target())} once sent"
        with wx.GenericProgressDialog("Shrinking the video", message, maximum=100, parent=self,
                                      style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME) as dialog:
            while not state["done"]:
                keep_going, _skip = dialog.Update(min(int(state["fraction"] * 100), 99))
                if not keep_going:
                    state["cancel"] = True
                wx.MilliSleep(100)
        if state["cancel"] or state["error"] is not None:
            shutil.rmtree(folder, ignore_errors=True)
            if isinstance(state["error"], ShrinkCancelled) or state["cancel"]:
                self.status.SetLabel("Shrinking the video was cancelled.")
            else:
                wx.MessageBox(str(state["error"]), "Shrinking failed", wx.OK | wx.ICON_ERROR)
            return None
        return target

    def _download_dir(self) -> Path | None:
        """Returns a usable download folder, asking for a new default when it is not available."""
        if self.config.download_dir:
            folder, available = Path(self.config.download_dir), Path(self.config.download_dir).is_dir()
            reason = f"The download folder {folder} does not exist."
        else:
            # Only the Videos folder has to exist; the scene-access subfolder is created.
            folder = default_download_dir()
            available = folder.parent.is_dir()
            reason = f"The Videos folder {folder.parent} does not exist."
        if available:
            try:
                folder.mkdir(exist_ok=True)
                return folder
            except OSError as error:
                reason = f"The download folder {folder} could not be created: {error}"
        with wx.DirDialog(self, f"{reason} Choose the default download folder:", style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            folder = Path(dialog.GetPath())
        self.config = replace(self.config, download_dir=str(folder))
        save_config(self.config_path, self.config)
        return folder

    def _download_with_progress(self, url: str, target_dir: Path, max_height: int) -> tuple[Path, str, dict[str, Any]] | None:
        filename_fields = self.config.filename_fields
        state: dict[str, Any] = {"percent": 0, "done": False, "cancel": False, "result": None, "error": None}

        def progress(update: dict[str, Any]) -> None:
            if state["cancel"]:
                raise DownloadCancelled()
            total = update.get("total_bytes") or update.get("total_bytes_estimate")
            if update.get("status") == "downloading" and total:
                state["percent"] = int(update.get("downloaded_bytes", 0) * 100 / total)

        def worker() -> None:
            try:
                state["result"] = download_video(url, target_dir, max_height, progress, filename_fields,
                                                 self.config.youtube_cookies)
            except Exception as error:
                state["error"] = error
            finally:
                state["done"] = True

        existing = set(target_dir.iterdir())
        announce("Downloading video…")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        message = f"Downloading the video (up to {max_height}p) to {target_dir}"
        # The native Windows progress dialog runs in its own thread and may not get the focus, so a screen
        # reader does not announce it until it closes. The generic one is a normal dialog on the main thread.
        with wx.GenericProgressDialog("Downloading from YouTube", message, maximum=100, parent=self,
                                      style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME) as dialog:
            while not state["done"]:
                # Video and audio are separate downloads, so the percentage restarts once.
                keep_going, _skip = dialog.Update(min(state["percent"], 99))
                if not keep_going:
                    state["cancel"] = True
                wx.MilliSleep(100)
        thread.join()
        if state["cancel"] or state["error"] is not None:
            # Remove only the partial files this download created, never anything that was there before.
            for leftover in set(target_dir.iterdir()) - existing:
                try:
                    leftover.unlink()
                except OSError:
                    pass
        if state["cancel"]:
            self.status.SetLabel("The download was cancelled.")
            return None
        if state["error"] is not None:
            if needs_sign_in(state["error"]) and self._ask_youtube_sign_in():
                return self._download_with_progress(url, target_dir, max_height)
            self.status.SetLabel("The video could not be downloaded.")
            wx.MessageBox(str(state["error"]), "Download failed", wx.OK | wx.ICON_ERROR)
            return None
        return state["result"]

    def _with_youtube_sign_in(self, call: Callable[[str], Any]) -> Any:
        """Runs a YouTube request with the configured cookies; if YouTube asks to sign in, asks where the
        user is signed in and tries again."""
        while True:
            try:
                with wx.BusyCursor():
                    return call(self.config.youtube_cookies)
            except Exception as error:
                if not (needs_sign_in(error) and self._ask_youtube_sign_in()):
                    raise

    def _ask_youtube_sign_in(self) -> bool:
        """Asks where the user is signed in to YouTube after YouTube asked to sign in. True to try again."""
        reason = ("YouTube asks to sign in to confirm that this is not a bot."
                  + (f" The current choice ({cookies_description(self.config.youtube_cookies)}) was not enough."
                     if self.config.youtube_cookies else ""))
        choice = ask_youtube_sign_in(self, reason)
        if choice is None:
            return False
        self.config = replace(self.config, youtube_cookies=choice)
        save_config(self.config_path, self.config)
        return True

    def _ask_first_question(self, online: bool, previous: tuple[str, str] | None = None) -> tuple[str, str] | None:
        """Returns the first question and the chosen profile's name, or None if cancelled. previous refills
        the dialog when the user came back from the size question."""
        question, profile = previous or ("Describe this video.", self.config.profile)
        config = self.config
        with StartChatDialog(self, config.profiles, profile or NO_PROFILE, online=online, max_height=config.youtube_max_height,
                             auto_shrink=config.auto_shrink, shrink_target_mb=config.shrink_target_mb,
                             question=question) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            question, profile = dialog.value()
            # The profile and the advanced settings are preselected next time.
            chosen = replace(config, profile=profile or NO_PROFILE, youtube_max_height=dialog.max_height,
                             auto_shrink=dialog.auto_shrink, shrink_target_mb=dialog.shrink_target_mb)
        if chosen != config:
            self.config = chosen
            save_config(self.config_path, self.config)
        return (question, profile) if question else None

    def _start_chat(self, source: Path, title: str, question: str, profile: str, *, source_url: str = "",
                    video_info: dict[str, Any] | None = None, sent_video: Path | None = None) -> None:
        skip_model, self.pending_size_skip = self.pending_size_skip, ""
        try:
            # Reading a large video can take a while, and one that does not fit into memory fails here.
            with wx.BusyCursor():
                self.current = self.store.create_session(
                    owner_id=self.owner_id, source_path=source, title=title, model=self.config.model, initial_question=question,
                    # Frozen for this chat: they precede the video in every request (see VideoSession).
                    system_prompt=system_prompt(self.config.profile_text(profile)),
                    tools=tool_definitions(online_video=bool(source_url)), source_url=source_url, video_info=video_info,
                    # The first request decides whether this model needs a transcript of the sound.
                    transcript_state=TRANSCRIPT_PENDING, audio_only=self.config.audio_only_auto, sent_video=sent_video)
        except (OSError, MemoryError) as error:
            wx.MessageBox(f"The video could not be loaded: {error or type(error).__name__}", "Video not loaded", wx.OK | wx.ICON_ERROR)
            return
        finally:
            if sent_video and sent_video != source:
                # A shrunk copy; the chat now keeps it as its stored video.
                shutil.rmtree(sent_video.parent, ignore_errors=True)
        self.size_skip_after_answer = (self.current.id, skip_model) if skip_model else None
        self.refresh_sessions()
        self.render_current()
        self._send(question, initial=True)
