from __future__ import annotations

import argparse
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEB_DIR = ROOT / "web_dashboard"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.runtime_maintenance import is_cloud_io_error, materialize_file


class NoCacheHTTPRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_file():
            materialize_file(path)
        return super().send_head()

    def copyfile(self, source, outputfile) -> None:
        while True:
            try:
                chunk = source.read(256 * 1024)
            except OSError as error:
                if not is_cloud_io_error(error):
                    raise
                source_name = getattr(source, "name", None)
                if source_name:
                    materialize_file(Path(source_name))
                time.sleep(0.05)
                continue
            if not chunk:
                break
            outputfile.write(chunk)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the local dashboard without browser caching.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--directory", type=Path, default=DEFAULT_WEB_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    handler = partial(NoCacheHTTPRequestHandler, directory=str(args.directory))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {args.directory} on http://{args.host}:{args.port}/ with no-store cache headers")
    server.serve_forever()


if __name__ == "__main__":
    main()
