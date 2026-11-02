"""Run everything with one command: `python -m kivilcim`.

Starts the web server and, in the background, keeps the data fresh by fetching new satellite
detections every few minutes. Needs FIRMS_MAP_KEY in `.env` for the automatic fetching.
"""

import argparse
import logging
import sys

import uvicorn

from kivilcim.api import create_app
from kivilcim.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kivilcim", description=__doc__.strip().splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1; use 0.0.0.0 to share)")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default 8000)")
    parser.add_argument("--no-ingest", action="store_true", help="serve the stored data only; do not fetch new detections")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    app = create_app(settings=settings, run_scheduler=not args.no_ingest)
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    # ASCII only: some console code pages cannot print the dotless i and would crash the start.
    print(f"Kivilcim is running at http://{shown}:{args.port}/   (stop with Ctrl+C)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
