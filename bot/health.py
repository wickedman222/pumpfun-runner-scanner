from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("runner")

STATUS = {
    "ok": True,
    "service": "pumpfun-runner-scanner",
    "seen": 0,
    "posted": 0,
    "attention": 0,
    "last_error": "",
    "paper_equity": 0,
    "quota": 0,
    "watches": 0,
    "feeds": {},
    "tape": {},
    "smart_wallets": 0,
}


def start(port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps(STATUS).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server on :%s", port)
