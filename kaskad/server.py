"""A small JSON endpoint for the assistant (standard library http.server).

    POST /ask     {"text": "...", "session": "..."}  ->  the result of Assistant.ask()
    GET  /health  ->  {"ok": true, "modules": [...], "errors": [...]}

It listens on 127.0.0.1 by default and has no authentication: keep it local, or put a reverse
proxy with authentication in front of it before other machines can reach it.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024


def make_server(assistant, host="127.0.0.1", port=8765, quiet=True):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kaskad"

        def _send(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - name fixed by http.server
            if self.path == "/health":
                self._send(200, {"ok": True, "modules": assistant.modules, "errors": assistant.errors})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802 - name fixed by http.server
            if self.path != "/ask":
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("content-length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return self._send(400, {"error": "empty body"})
            if length > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "body is not JSON"})
            if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not data["text"].strip():
                return self._send(400, {"error": "text is required"})
            session = str(data.get("session") or "default")[:200]
            return self._send(200, assistant.ask(data["text"], session))

        def log_message(self, format, *args):  # noqa: A002 - signature fixed by http.server
            if not quiet:
                BaseHTTPRequestHandler.log_message(self, format, *args)

    return ThreadingHTTPServer((host, port), Handler)
