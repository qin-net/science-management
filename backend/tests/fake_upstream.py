"""A local stand-in for arXiv, OpenAlex and Crossref, reached over real sockets.

By default it replays the responses recorded on 2026-10-31 (tests/fixtures/sources). A test
installs a handler to make one source slow, rate limited, truncated and so on. Every request is
logged with its raw request line and the times it started and finished. The three sources share
one server and are told apart by a path prefix: /arxiv, /openalex, /crossref.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
SOURCES = ("arxiv", "openalex", "crossref")

ARXIV_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" '
    'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
)
ARXIV_EMPTY = ARXIV_HEAD + "<opensearch:totalResults>0</opensearch:totalResults></feed>"
ARXIV_ERROR = (
    ARXIV_HEAD + "<opensearch:totalResults>1</opensearch:totalResults><entry>"
    "<id>https://arxiv.org/api/errors#incorrect_id_format_for_notanid</id>"
    "<title>Error</title><summary>incorrect id format for notanid</summary></entry></feed>"
)


@dataclass
class Reply:
    status: int = 200
    body: bytes | str = b""
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    drop: bool = False  # close the socket without answering
    cut_after: int | None = None  # send only this many body bytes, then close


@dataclass
class Seen:
    request_line: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    started: float
    finished: float = 0.0


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _recorded_headers(name: str) -> dict[str, str]:
    pairs = (line.split(": ", 1) for line in _fixture(name).decode().splitlines() if ": " in line)
    return {key: value for key, value in pairs}


def _json_404(message: str) -> Reply:
    return Reply(404, f'{{"error": "{message}"}}', {"Content-Type": "application/json"})


def default_arxiv(seen: Seen) -> Reply:
    query = seen.query
    ids = query.get("id_list", [""])[0]
    if ids:
        if ids == "notanid":
            return Reply(body=ARXIV_ERROR)
        return Reply(body=_fixture("arxiv-1706.03762.xml") if ids == "1706.03762" else ARXIV_EMPTY)
    if not query.get("search_query", [""])[0]:
        return Reply(body=ARXIV_ERROR)
    return Reply(body=_fixture("arxiv-search.xml"))


def default_openalex(seen: Seen) -> Reply:
    headers = {"Content-Type": "application/json", **_recorded_headers("openalex-headers.txt")}
    if seen.path.rstrip("/") == "/openalex/works":
        return Reply(body=_fixture("openalex-search.json"), headers=headers)
    return _json_404("not found")


def default_crossref(seen: Seen) -> Reply:
    headers = {"Content-Type": "application/json", **_recorded_headers("crossref-headers.txt")}
    if seen.path.rstrip("/") == "/crossref/works":
        return Reply(body=_fixture("crossref-search.json"), headers=headers)
    if seen.path == "/crossref/works/10.1038/nature14539":
        return Reply(body=_fixture("crossref-work.json"), headers=headers)
    return Reply(404, "Resource not found.", {"Content-Type": "text/plain"})


Handler = Callable[[Seen], Reply]


class FakeUpstream:
    def __init__(self) -> None:
        self.handlers: dict[str, Handler] = {
            "arxiv": default_arxiv,
            "openalex": default_openalex,
            "crossref": default_crossref,
        }
        self.seen: dict[str, list[Seen]] = {name: [] for name in SOURCES}
        self.lock = threading.Lock()
        fake = self

        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                source = parts.path.strip("/").split("/")[0]
                seen = Seen(
                    request_line=self.requestline,
                    path=unquote(parts.path),
                    query=parse_qs(parts.query, keep_blank_values=True),
                    headers={k.lower(): v for k, v in self.headers.items()},
                    started=time.monotonic(),
                )
                with fake.lock:
                    fake.seen.setdefault(source, []).append(seen)
                reply = fake.handlers[source](seen) if source in fake.handlers else Reply(404)
                try:
                    self._send(reply)
                finally:
                    seen.finished = time.monotonic()

            def _send(self, reply: Reply) -> None:
                if reply.delay:
                    time.sleep(reply.delay)
                if reply.drop:
                    self.close_connection = True
                    self.connection.close()
                    return
                body = reply.body.encode() if isinstance(reply.body, str) else reply.body
                self.send_response(reply.status)
                for key, value in reply.headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body if reply.cut_after is None else body[: reply.cut_after])
                if reply.cut_after is not None:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "FakeUpstream":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def count(self, source: str) -> int:
        return len(self.seen[source])

    def set(self, source: str, handler: Handler | Reply) -> None:
        self.handlers[source] = handler if callable(handler) else (lambda _seen: handler)
