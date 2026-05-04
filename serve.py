#!/usr/bin/env python3
"""
Static file server for scraped Framer sites.

Handles Framer CMS ?range=start-end,start-end query params: the JS client
sets this parameter and expects the response body to contain exactly the
concatenated byte slices (status 200, length == sum of all ranges).
"""

import os
import sys
import mimetypes
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SERVE_DIR = os.path.join(os.path.dirname(__file__), "scrap", "meetsmeet.framer.website")
PORT = 8000


class FramerHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        # Strip query string before resolving file path
        path = urlparse(path).path
        # Delegate to parent using the query-stripped path
        self.path = path
        result = super().translate_path(path)
        return result

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "range" not in params:
            # Normal file serve
            self.path = parsed.path
            super().do_GET()
            return

        # Handle ?range=start-end[,start-end,...]
        file_path = self.translate_path(parsed.path)
        if not os.path.isfile(file_path):
            self.send_error(404, "File not found")
            return

        range_str = params["range"][0]  # e.g. "0-144" or "0-144,200-300"
        try:
            ranges = []
            for part in range_str.split(","):
                start_s, end_s = part.strip().split("-")
                start = int(start_s)
                end = int(end_s)  # inclusive end (Framer uses from-to-1)
                ranges.append((start, end + 1))  # convert to exclusive end
        except Exception:
            self.send_error(400, "Bad range parameter")
            return

        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(500, "Could not read file")
            return

        chunks = []
        for start, end in ranges:
            chunks.append(data[start:end])
        body = b"".join(chunks)

        ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Suppress 200s for cleaner output; show only errors
        if args and len(args) >= 2 and not str(args[1]).startswith("2"):
            super().log_message(fmt, *args)
        elif os.environ.get("VERBOSE"):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    os.chdir(SERVE_DIR)
    server = HTTPServer(("", port), FramerHandler)
    print(f"Serving {SERVE_DIR}")
    print(f"Open http://localhost:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
