import subprocess
import sys
import threading
import time

import wx


def test_second_process_activates_primary_without_becoming_primary(tmp_path):
    from sceneaccess.single_instance import SingleInstance
    app = wx.App(False)
    primary = SingleInstance(tmp_path)
    activated = threading.Event()
    try:
        assert primary.is_primary
        primary.start(activated.set)
        script = '''import sys, wx
from pathlib import Path
from sceneaccess.single_instance import SingleInstance
app = wx.App(False)
instance = SingleInstance(Path(sys.argv[1]))
print(instance.is_primary)
print(instance.activate_existing(timeout=2))
instance.close()
app.Destroy()
'''
        result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ['False', 'True']
        assert activated.wait(2)
    finally:
        primary.close()
        app.Destroy()


def test_primary_shutdown_releases_lock_and_endpoint(tmp_path):
    from sceneaccess.single_instance import SingleInstance
    app = wx.App(False)
    first = SingleInstance(tmp_path)
    try:
        first.start(lambda: None)
        first.close()
        second = SingleInstance(tmp_path)
        try:
            assert second.is_primary
            assert not second.port_path.exists()
        finally:
            second.close()
    finally:
        first.close()
        app.Destroy()


def test_relaunch_activates_existing_gui_and_exits(tmp_path):
    ready, activated = tmp_path/'ready', tmp_path/'activated'
    primary_script = '''import sys, wx
from pathlib import Path
from sceneaccess import gui
root = Path(sys.argv[1])
gui.config_path_from_standard_paths = lambda _: root/'config.json'
original_activate = gui.ChatWindows.activate
def activate(self):
    original_activate(self)
    (root/'activated').write_text('activated')
    for frame in list(self.frames): frame.Close()
gui.ChatWindows.activate = activate
original_loop = wx.App.MainLoop
def loop(self):
    (root/'ready').write_text('ready')
    wx.CallLater(15000, self.ExitMainLoop)
    return original_loop(self)
wx.App.MainLoop = loop
raise SystemExit(gui.main())
'''
    secondary_script = '''import sys
from pathlib import Path
from sceneaccess import gui
gui.config_path_from_standard_paths = lambda _: Path(sys.argv[1])/'config.json'
raise SystemExit(gui.main())
'''
    primary = subprocess.Popen([sys.executable, '-c', primary_script, str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic()+10
        while not ready.exists() and primary.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), 'Primary GUI did not start'
        secondary = subprocess.run([sys.executable, '-c', secondary_script, str(tmp_path)], capture_output=True, text=True, timeout=10)
        assert secondary.returncode == 0, secondary.stderr
        output, errors = primary.communicate(timeout=10)
        assert primary.returncode == 0, errors
        assert activated.read_text() == 'activated'
        assert not (tmp_path/'instance.port').exists()
    finally:
        if primary.poll() is None:
            primary.kill()
            primary.communicate(timeout=5)
