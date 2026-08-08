"""Command-line interface.

Exit codes:

===  ==========================================================
0    Audit completed; nothing exceeded ``--fail-over``.
1    Flagged waste exceeded ``--fail-over`` (CI budget gate).
2    Usage error, missing logs, or an Admin API failure.
===  ==========================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .analyze import audit
from .config import Config, ConfigError, load_config
from .detectors import KINDS, SUMMARIES, select
from .pricing import PriceBook
from .render import html, json_report, terminal
from .term import Style
from .transcripts import load
from .types import Report

DEFAULT_LOG_DIR = "~/.claude/projects"
DEFAULT_DAYS = 30
DEFAULT_TOP = 10

EXIT_OK = 0
EXIT_OVER_BUDGET = 1
EXIT_USAGE = 2

log = logging.getLogger("proctor")


def build_parser() -> argparse.ArgumentParser:
    kinds = "\n".join(f"    {k:<16} {SUMMARIES[k]}" for k in KINDS)
    parser = argparse.ArgumentParser(
        prog="proctor",
        description="Audit your Claude Code and Anthropic API usage for token waste.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"finding kinds (for --only / --skip):\n{kinds}",
    )
    parser.add_argument(
        "logdirs",
        nargs="*",
        metavar="PATH",
        help=f"transcript files or directories (default: {DEFAULT_LOG_DIR})",
    )
    parser.add_argument(
        "--days",
        type=int,
        help=f"how far back to look (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--top",
        type=int,
        help=f"rows per table (default: {DEFAULT_TOP})",
    )
    parser.add_argument(
        "--only",
        metavar="KIND",
        action="append",
        default=[],
        choices=KINDS,
        help="run only this finding kind (repeatable)",
    )
    parser.add_argument(
        "--skip",
        metavar="KIND",
        action="append",
        default=[],
        choices=KINDS,
        help="skip this finding kind (repeatable)",
    )
    parser.add_argument(
        "--project",
        metavar="SUBSTRING",
        help="only audit sessions whose project path contains this substring",
    )

    output = parser.add_argument_group("output")
    output.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    output.add_argument(
        "--html", metavar="FILE", help="also write a standalone HTML dashboard"
    )
    output.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    output.add_argument(
        "-v", "--verbose", action="store_true", help="log parsing detail to stderr"
    )

    org = parser.add_argument_group("organization")
    org.add_argument(
        "--admin",
        action="store_true",
        help="report org-wide usage via the Admin API (needs ANTHROPIC_ADMIN_KEY)",
    )
    org.add_argument(
        "--admin-only",
        action="store_true",
        help="run the Admin API report and skip the local transcript audit",
    )

    ci = parser.add_argument_group("automation")
    ci.add_argument(
        "--fail-over",
        type=float,
        metavar="USD",
        help="exit 1 if flagged waste exceeds this amount",
    )
    ci.add_argument("--config", metavar="FILE", help="path to a JSON config file")

    parser.add_argument("--version", action="version", version=f"proctor {__version__}")
    return parser


def _first_set(*candidates: int | None) -> int:
    """First non-``None`` value. Unlike ``or``, this treats 0 as a real value."""
    for value in candidates:
        if value is not None:
            return value
    raise ValueError("no default supplied")


def resolve_paths(args_dirs: Sequence[str], config: Config) -> list[Path]:
    raw = list(args_dirs) or config.log_dirs or [DEFAULT_LOG_DIR]
    return [Path(d).expanduser() for d in raw]


def _run_admin(args, prices: PriceBook, style: Style, stream) -> int:
    from . import admin  # imported lazily: the audit path needs no network code

    days = args.days or DEFAULT_DAYS
    try:
        report = admin.fetch(days)
    except admin.AdminError as exc:
        print(f"proctor: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        json.dump(admin.to_dict(report, prices), stream, indent=2)
        stream.write("\n")
    else:
        admin.render(report, stream, style, prices)
    return EXIT_OK


def run(argv: Sequence[str] | None = None, stream=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    stream = stream or sys.stdout

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="proctor: %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        config = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"proctor: {exc}", file=sys.stderr)
        return EXIT_USAGE

    style = Style.detect(force_off=args.no_color or args.json)
    prices = config.price_book()
    days = _first_set(args.days, config.days, DEFAULT_DAYS)
    top = _first_set(args.top, config.top, DEFAULT_TOP)

    if days < 1:
        print("proctor: --days must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    if top < 1:
        print("proctor: --top must be at least 1", file=sys.stderr)
        return EXIT_USAGE

    if args.admin or args.admin_only:
        status = _run_admin(args, prices, style, stream)
        if args.admin_only or status != EXIT_OK:
            return status

    paths = resolve_paths(args.logdirs, config)
    existing = [p for p in paths if p.exists()]
    if not existing:
        listed = ", ".join(str(p) for p in paths)
        print(
            f"proctor: no transcript path found (looked in {listed}).\n"
            f"         Pass the path to your Claude Code logs explicitly.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    since = datetime.now(timezone.utc) - timedelta(days=days)
    corpus = load(existing, since)

    if args.project:
        needle = args.project.lower()
        corpus.sessions = {
            sid: s for sid, s in corpus.sessions.items() if needle in s.project.lower()
        }

    if not corpus.with_turns():
        where = ", ".join(str(p) for p in existing)
        print(
            f"proctor: no API turns found in the last {days} days under {where}.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    report = audit(
        corpus,
        prices=prices,
        detectors=select(args.only, args.skip),
        top=top,
        days=days,
    )

    if args.json:
        json_report.render(report, stream, top)
    else:
        terminal.render(report, stream, style, top)

    if args.html:
        html.render(report, Path(args.html).expanduser(), top)
        if not args.json:
            stream.write(f"HTML report written to {args.html}\n")

    return _budget_status(report, args.fail_over)


def _budget_status(report: Report, threshold: float | None) -> int:
    if threshold is None:
        return EXIT_OK
    if report.flagged_waste > threshold:
        print(
            f"proctor: flagged waste ${report.flagged_waste:,.2f} exceeds "
            f"--fail-over ${threshold:,.2f}",
            file=sys.stderr,
        )
        return EXIT_OVER_BUDGET
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:  # pragma: no cover
        print("\nproctor: interrupted", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
