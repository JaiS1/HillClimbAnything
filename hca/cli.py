"""The `hca` command line. Subcommands are imported lazily so `hca --help` stays instant."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .core import HcaError

EXIT_USAGE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hca", description="HillClimbAnything: a noise-aware referee for hill-climbing any metric.")
    parser.add_argument("--version", action="version", version=f"hca {__version__}")
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")
    from .commands import doctor, quick
    quick.add_arguments(sub.add_parser("quick", help="measure how noisy a command is (A/A), no spec needed",
                                       description="Usage: hca quick [options] -- <cmd...>"))
    doctor.add_arguments(sub.add_parser("doctor", help="check the environment for things that corrupt measurements"))
    from .commands import confirm, init, report, try_
    init.add_arguments(sub.add_parser("init", help="validate hca.toml, create the branch, run the baseline A/A"))
    try_.add_arguments(sub.add_parser("try", help="measure the current edits against the last keep; keep or revert"))
    confirm.add_arguments(sub.add_parser("confirm", help="re-measure the cumulative change against the original (held-out if any)"))
    report.add_report_arguments(sub.add_parser("report", help="print the execution report"))
    report.add_status_arguments(sub.add_parser("status", help="print the short state"))
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tail = None
    if "--" in argv:                         # everything after `--` is the command to measure, untouched
        cut = argv.index("--")
        argv, tail = argv[:cut], argv[cut + 1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    if tail is not None:
        args.command = tail
    if args.cmd is None:
        parser.print_help()
        return 0
    try:
        if args.cmd == "quick":
            from .commands import quick
            return quick.run(args)
        if args.cmd == "doctor":
            from .commands import doctor
            return doctor.run(args)
        if args.cmd == "init":
            from .commands import init
            return init.run(args)
        if args.cmd == "try":
            from .commands import try_
            return try_.run(args)
        if args.cmd == "confirm":
            from .commands import confirm
            return confirm.run(args)
        if args.cmd == "report":
            from .commands import report
            return report.run(args)
        if args.cmd == "status":
            from .commands import report
            return report.run_status(args)
        parser.print_help()
        return EXIT_USAGE
    except HcaError as exc:
        print(f"hca: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("hca: interrupted", file=sys.stderr)
        return 130
