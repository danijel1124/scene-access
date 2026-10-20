import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sceneaccess.openrouter import OpenRouterClient, OpenRouterError
from sceneaccess.request_control import RequestControl, RequestCancelled


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_cancel_interrupts_real_http_wait_without_waiting_for_provider(phase):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if phase == "body":
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
            entered.set()
            release.wait(5)
            try:
                if phase == "headers":
                    self.send_response(200)
                    self.end_headers()
                self.wfile.write(b'{}')
            except OSError:
                pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    control, errors = RequestControl(), []
    def request():
        try:
            OpenRouterClient('test-only', base_url=f'http://127.0.0.1:{server.server_port}/chat', control=control)._post({})
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()
    worker = threading.Thread(target=request)
    worker.start()
    try:
        assert entered.wait(2)
        control.cancel()
        assert finished.wait(2), 'Cancellation must interrupt the socket, not just ignore a later result'
        assert len(errors) == 1 and isinstance(errors[0], RequestCancelled)
    finally:
        release.set()
        worker.join(5)
        server.shutdown()
        server.server_close()
        serving.join(2)


def test_controlled_transport_honors_http_proxy(monkeypatch):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        def log_message(self, format, *args): pass
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=proxy.serve_forever, daemon=True)
    serving.start()
    monkeypatch.setenv('http_proxy', f'http://127.0.0.1:{proxy.server_port}')
    monkeypatch.setenv('no_proxy', '')
    try:
        client = OpenRouterClient('fixture', base_url='http://target.invalid/chat', control=RequestControl())
        assert client._post({}) == {'ok': True}
        assert seen == ['http://target.invalid/chat']
    finally:
        proxy.shutdown()
        proxy.server_close()
        serving.join(2)


@pytest.mark.parametrize('status', [200, 400])
def test_controlled_transport_reads_json_and_provider_error(status):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True} if status == 200 else {'error': {'message': 'Fixture error'}}).encode())
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        client = OpenRouterClient('test-only', base_url=f'http://127.0.0.1:{server.server_port}/chat', control=RequestControl())
        if status == 200:
            assert client._post({}) == {'ok': True}
        else:
            with pytest.raises(OpenRouterError, match='Fixture error'):
                client._post({})
    finally:
        server.shutdown()
        server.server_close()
        serving.join(2)
