"""A local stand-in for POST /v1/messages, reached over real sockets (same shape as
tests/fake_upstream.py, kept separate because the request/response shape is unrelated)."""

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class ModelReply:
    status: int = 200
    body: dict | None = None
    headers: dict[str, str] = field(default_factory=dict)
    drop: bool = False


@dataclass
class Seen:
    body: dict
    headers: dict[str, str]


class FakeModel:
    """`queue_reply` sets what the next call(s) get back; `calls` records every request body."""

    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._replies: list[ModelReply] = []
        self.calls: list[Seen] = []
        # What every call gets once the queue is empty (the browser tests set this).
        self.default = ModelReply()

    def queue_reply(self, reply: ModelReply) -> None:
        with self._lock:
            self._replies.append(reply)

    def reset(self) -> None:
        with self._lock:
            self._replies.clear()
            self.calls.clear()
            self.default = ModelReply()

    def start(self) -> "FakeModel":
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # quiet
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {"_unparsable": raw.decode("utf-8", "replace")}
                with fake._lock:
                    fake.calls.append(Seen(body=body, headers=dict(self.headers)))
                    reply = fake._replies.pop(0) if fake._replies else fake.default
                if reply.drop:
                    self.connection.close()
                    return
                payload = json.dumps(reply.body or {}).encode()
                self.send_response(reply.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                for key, value in reply.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()


def message_reply(
    *, text: str, stop_reason: str = "end_turn", model: str = "claude-sonnet-5-5", **usage
) -> ModelReply:
    return ModelReply(
        body={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": usage.get("input_tokens", 10),
                "output_tokens": usage.get("output_tokens", 5),
            },
        }
    )


def answer(answerable: bool, segments: list[dict]) -> str:
    return json.dumps({"answerable": answerable, "segments": segments})
