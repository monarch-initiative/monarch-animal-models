"""A small static server for previewing dist/ with gzip, like GitHub Pages.

Python's http.server sends files uncompressed; the browser index is ~14 MB raw
and ~1.4 MB gzipped, which is the difference between instant and painfully slow
over a LAN or Wi-Fi.
"""

from __future__ import annotations

import gzip
import socket
from functools import lru_cache, partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COMPRESSIBLE = {".html", ".js", ".css", ".json", ".svg", ".txt"}


@lru_cache(maxsize=256)
def _gzipped(path: str, mtime: float) -> bytes:
    return gzip.compress(Path(path).read_bytes(), compresslevel=6)


class GzipHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        path = Path(self.translate_path(self.path))
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        if not (accepts_gzip and path.is_file() and path.suffix in COMPRESSIBLE):
            return super().send_head()
        body = _gzipped(str(path), path.stat().st_mtime)
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self._gzip_body = body
        return None

    def do_GET(self):
        self._gzip_body = None
        result = self.send_head()
        if self._gzip_body is not None:
            self.wfile.write(self._gzip_body)
        elif result:
            try:
                self.copyfile(result, self.wfile)
            finally:
                result.close()

    def do_HEAD(self):
        self._gzip_body = None
        result = self.send_head()
        if result:
            result.close()


class DualStackServer(ThreadingHTTPServer):
    """Listen on IPv6 and IPv4, so mDNS names that resolve to either work."""

    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def serve(directory: Path, port: int) -> None:
    handler = partial(GzipHandler, directory=str(directory.expanduser().resolve()))
    with DualStackServer(("::", port), handler) as server:
        print(f"Serving {directory} with gzip on http://{socket.gethostname()}.local:{port}/")
        server.serve_forever()
