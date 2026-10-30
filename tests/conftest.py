"""On Windows every test runs on its own invisible desktop: many tests open real wx windows, which would
otherwise appear on the user's screen, take the focus and confuse the screen reader."""

import sys

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
