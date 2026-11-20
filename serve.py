"""Publish the site, serve it locally and open it in your browser.

    python serve.py            (or the "Play demo" run configuration)

It runs publish_site.py first, so what you play is exactly what GitHub
Pages would serve from docs/, built from your latest results/. The one
difference is config.json: locally, finished games are saved by this server
into playtests/ rather than sent to the Google Sheet, and ?date=YYYY-MM-DD
can preview a scheduled day.

Why a server at all: browsers won't let a page opened straight from disk
(file://) fetch other files. Python's built-in web server is enough; nothing
to install. It serves the whole project folder (so the browser tests in
game/ can reach tests/), and it only listens on this computer (127.0.0.1),
so nobody else on the network can see it.

It also accepts one kind of upload: a finished game's playtest record,
which the page sends to /playtest and this saves in playtests/ for
playtest_report.py to summarise (DECISIONS.md, 35).

Stop it with the red square in IntelliJ, or Ctrl+C in a terminal.
"""

import argparse
import functools
import http.server
import json
import re
import socket
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PLAYTESTS = ROOT / "playtests"
MAX_UPLOAD = 2_000_000   # bytes; a long game's record is a few kilobytes


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files with caching turned off, so an edit shows up on reload."""

    # Make sure modules and JSON get the right types on every platform.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".json": "application/json",
        ".css": "text/css",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        """Files as they are, except the site settings, which are the local ones."""
        if self.path.split("?")[0] == "/docs/config.json":
            from publish_site import site_settings
            body = json.dumps(site_settings(local=True)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        """Save a playtest record sent by the page to playtests/<file>.json."""
        if self.path != "/playtest":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_UPLOAD:
            self.send_error(413, "Playtest record missing or too big")
            return
        try:
            record = json.loads(self.rfile.read(length))
        except ValueError:
            self.send_error(400, "Not JSON")
            return
        # Only letters, digits, dots, dashes and underscores in the file name,
        # so a record can't be written anywhere but playtests/.
        name = re.sub(r"[^A-Za-z0-9._-]", "-", str(record.get("file") or "playtest"))[:100]
        name = name if name.endswith(".json") else name + ".json"
        PLAYTESTS.mkdir(exist_ok=True)
        path, n = PLAYTESTS / name, 2
        while path.exists():                       # never overwrite an earlier one
            path, n = PLAYTESTS / name.replace(".json", f"-{n}.json"), n + 1
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Saved playtest: playtests/{path.name}")
        body = json.dumps({"saved": f"playtests/{path.name}"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Only log problems; a line per file fetched is just noise.
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


def free_port(start: int) -> int:
    """The first port from `start` upwards that nothing else is using."""
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((HOST, port)) != 0:
                return port
    sys.exit(f"No free port between {start} and {start + 49}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000, help="first port to try (default 8000)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    # Publish first, so the page plays the latest builds.
    sys.path.insert(0, str(ROOT))
    from publish_site import publish
    summary = publish()
    if not summary["bank"]:
        print("No prompts are ready and built yet. Run 'Build bank prompts', then this again.")

    port = free_port(args.port)
    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer((HOST, port), handler)
    url = f"http://{HOST}:{port}/docs/"
    print(f"\nParts Per Million demo: {url}")
    print(f"A scheduled day:        {url}?mode=daily&date=YYYY-MM-DD")
    print(f"Browser tests:          http://{HOST}:{port}/game/tests.html")
    print("Stop with the red square (IntelliJ) or Ctrl+C.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
