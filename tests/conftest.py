"""Shared test setup. On Windows every test runs on its own invisible desktop: many tests open real wx
windows, which would otherwise appear on the user's screen, take the focus and confuse the screen reader.
Also fixtures that several test files use."""

import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import imageio_ffmpeg
import pytest

TEST_DESKTOP = "sceneaccess-tests"


def _move_to_invisible_desktop() -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CreateDesktopW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD,
                                      wintypes.DWORD, ctypes.c_void_p)
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.SetThreadDesktop.argtypes = (wintypes.HANDLE,)
    generic_all = 0x10000000
    desktop = user32.CreateDesktopW(TEST_DESKTOP, None, None, 0, generic_all, None)
    # Windows are created on the desktop of the thread that creates them; wx creates them on this thread.
    if not desktop or not user32.SetThreadDesktop(desktop):
        pytest.exit(f"The tests could not move to an invisible desktop (Windows error {ctypes.get_last_error()}), "
                    "so they did not start: their windows would appear on the screen.", returncode=3)


def pytest_configure(config: pytest.Config) -> None:
    if sys.platform == "win32":
        _move_to_invisible_desktop()


@pytest.fixture
def video_server(tmp_path, request):
    """A local web server with a tiny real video, a page with two videos and a page with one."""
    ext = getattr(request, 'param', 'mp4')
    clip = tmp_path / ('fixture.' + ext)
    codec = 'libvpx-vp9' if ext == 'webm' else 'libx264'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=64x64:duration=0.3',
                    '-an', '-c:v', codec, str(clip)], check=True)
    payload = clip.read_bytes()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == '/index.html':
                body = (b'<html><head><title>Two videos</title></head><body>'
                        b'<video controls src="/one.mp4"></video>'
                        b'<video controls src="/two.mp4"></video></body></html>')
                content_type = 'text/html'
            elif self.path == '/single.html':
                body = (b'<html><head><title>One video</title></head><body>'
                        b'<video controls src="/one.mp4"></video></body></html>')
                content_type = 'text/html'
            else:
                body = payload
                content_type = {'mp4': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime'}[ext]
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
