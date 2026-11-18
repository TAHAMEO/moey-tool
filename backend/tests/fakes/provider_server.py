"""Local fake of OpenAI-compatible, Gemini and Anthropic HTTP APIs.

Used only for *contract* tests of CLIPFORGE's adapters and router (error
classification, fallback, budgets, caching). It reproduces documented request
and response shapes; it is not, and is never reported as, a real provider.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self


class FakeProviderServer:
    def __init__(self) -> None:
        self.behavior: dict[str, Any] = {"mode": "ok", "reply": "OK", "delay": 0.0}
        self.requests: list[dict[str, Any]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _send(self, status: int, payload: dict, headers: dict | None = None):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _fail_if_needed(self) -> bool:
                mode = server.behavior.get("mode")
                if mode == "rate_limit":
                    self._send(429, {"error": {"message": "Rate limit reached"}}, {"Retry-After": "7"})
                    return True
                if mode == "auth":
                    self._send(401, {"error": {"message": "Incorrect API key provided"}})
                    return True
                if mode == "server_error":
                    self._send(503, {"error": {"message": "overloaded"}})
                    return True
                return False

            def do_GET(self):
                server.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
                if self._fail_if_needed():
                    return
                if self.path.startswith("/v1/models"):
                    ids = server.behavior.get("models", ["fake-small-1b", "fake-pro-70b"])
                    if "/anthropic" in self.path:
                        self._send(200, {"data": [{"id": i} for i in ids]})
                    else:
                        self._send(200, {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})
                elif self.path.startswith("/v1beta/models"):
                    self._send(200, {"models": [{"name": "models/gemini-2.5-flash", "inputTokenLimit": 1048576,
                                                 "supportedGenerationMethods": ["generateContent"]}]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append({"method": "POST", "path": self.path, "body": body, "headers": dict(self.headers)})
                if server.behavior.get("delay"):
                    time.sleep(server.behavior["delay"])
                if self._fail_if_needed():
                    return
                reply = server.behavior.get("reply", "OK")
                if callable(reply):
                    reply = reply(body)
                if self.path.endswith("/chat/completions"):
                    self._send(200, {
                        "id": "chatcmpl-test", "object": "chat.completion", "model": body.get("model"),
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
                    })
                elif ":generateContent" in self.path:
                    self._send(200, {
                        "candidates": [{"content": {"role": "model", "parts": [{"text": reply}]}, "finishReason": "STOP"}],
                        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
                    })
                elif self.path.endswith("/messages"):
                    self._send(200, {
                        "id": "msg_test", "type": "message", "role": "assistant", "model": body.get("model"),
                        "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn",
                        "usage": {"input_tokens": 90, "output_tokens": 25},
                    })
                elif self.path.endswith("/embeddings"):
                    inputs = body.get("input") or []
                    self._send(200, {"data": [{"index": i, "embedding": [0.1, 0.2, 0.3]} for i, _ in enumerate(inputs)]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
