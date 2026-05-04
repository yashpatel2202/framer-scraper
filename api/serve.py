import os
import mimetypes
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

STATIC_DIR = os.path.realpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path in ('', '/'):
            path = '/index.html'

        file_path = os.path.realpath(os.path.join(STATIC_DIR, path.lstrip('/')))

        # Block path traversal
        if not file_path.startswith(STATIC_DIR):
            self.send_error(403, 'Forbidden')
            return

        # Serve index.html for bare directory paths
        if os.path.isdir(file_path):
            file_path = os.path.join(file_path, 'index.html')

        if not os.path.isfile(file_path):
            self.send_error(404, 'Not Found')
            return

        ctype = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'

        if 'range' not in params:
            with open(file_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # Handle Framer CMS ?range=start-end[,start-end,...] byte-slice requests
        range_str = params['range'][0]
        try:
            ranges = []
            for part in range_str.split(','):
                start_s, end_s = part.strip().split('-')
                ranges.append((int(start_s), int(end_s) + 1))  # inclusive → exclusive
        except Exception:
            self.send_error(400, 'Bad range parameter')
            return

        try:
            with open(file_path, 'rb') as f:
                data = f.read()
        except OSError:
            self.send_error(500, 'Could not read file')
            return

        body = b''.join(data[s:e] for s, e in ranges)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
