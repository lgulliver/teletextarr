"""Run Teletextarr: python -m teletextarr --config config.yaml"""
import argparse
import os
import sys

from . import config as config_module
from .pages import build
from .render import Renderer
from .server import serve
from .sources import Sources


def main():
    parser = argparse.ArgumentParser(prog="teletextarr", description="Teletext-style pages as live TV channels for Plex and Jellyfin.")
    parser.add_argument("--config", default=os.environ.get("TELETEXTARR_CONFIG", "/config/config.yaml"))
    parser.add_argument("--check", action="store_true", help="validate the config and exit")
    args = parser.parse_args()
    try:
        config = config_module.load(args.config)
    except (OSError, config_module.ConfigError) as e:
        sys.exit(f"config error: {e}")
    if args.check:
        print(f"config ok: {len(config.pages)} pages")
        return
    sources = Sources(config)
    sources.start()
    serve(config, Renderer(), lambda number: build(config, number, sources), sources)


if __name__ == "__main__":
    main()
