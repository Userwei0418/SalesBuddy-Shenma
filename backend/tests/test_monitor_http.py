from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

SOURCE=Path(os.environ.get('SALESBUDDY_SOURCE_ROOT',Path(__file__).resolve().parents[2]))
sys.path.append(str(SOURCE/'tools/installation'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/installation'))
from instance_files import InstanceError
from monitor_http import health_json, listener_owned


@contextmanager
def server(case):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            try:
                if case=='timeout': time.sleep(.3)
                if case=='slow_header':
                    for part in b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}':
                        self.connection.send(bytes([part])); time.sleep(.015)
                    return
                code=302 if case=='redirect' else 200
                self.send_response(code)
                if case=='redirect': self.send_header('Location','http://not-contacted.invalid/')
                if case=='gzip': self.send_header('Content-Encoding','gzip')
                body=(b'{' if case=='invalid' else b'[]' if case=='array' else b'{"status":"ok","status":"bad"}'
                      if case=='duplicate' else b'x'*65537 if case in {'large','large_stream'} else b'{"status":"ok"}')
                if case=='truncated': self.send_header('Content-Length',str(len(body)+4))
                elif case!='large_stream': self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                if case=='slow_body':
                    for byte in body: self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.05)
                else: self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError): pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
    try: yield httpd.server_port
    finally: httpd.shutdown(); httpd.server_close(); thread.join()


def test_health_request_ignores_proxy_environment_and_has_bounded_valid_json(monkeypatch):
    monkeypatch.setenv('http_proxy','http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY','http://127.0.0.1:1')
    with server('ok') as port: assert health_json(port,'live',timeout=.2)==(200,{'status':'ok'})


@pytest.mark.parametrize('case',['timeout','redirect','invalid','array','duplicate','large','large_stream',
                              'truncated','gzip','slow_header','slow_body'])
def test_invalid_or_unbounded_health_responses_are_rejected(case):
    with server(case) as port:
        started=time.monotonic()
        with pytest.raises((InstanceError,OSError)):
            health_json(port,'ready',timeout=.15)
        assert time.monotonic()-started<.6


def test_real_loopback_listener_must_belong_to_the_expected_pid():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); listener.listen(1)
        port=listener.getsockname()[1]
        assert listener_owned(port,os.getpid())
        assert not listener_owned(port,os.getpid()+100000)
