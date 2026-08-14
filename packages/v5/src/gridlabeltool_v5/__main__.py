from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gridlabeltool-v5",
        description="Launch the packaged v5 GridLabelTool layer-only GUI.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the package version and exit",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.version:
        from . import __version__

        print(__version__)
        return

    from .app import main as run_app

    run_app()


if __name__ == "__main__":
    main()

