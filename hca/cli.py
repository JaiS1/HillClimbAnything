"""The `hca` command line. Subcommands are imported lazily so `hca --help` stays instant."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .core import HcaError

NOT_YET = "not implemented yet (part B)"
EXIT_USAGE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hca", description="HillClimbAnything: a noise-aware referee for hill-climbing any metric.")
    parser.add_argument("--version", action="version", version=f"hca {__version__}")
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")
    from .commands import doctor, quick
    quick.add_arguments(sub.add_parser("quick", help="measure how noisy a command is (A/A), no spec needed",
                                       description="Usage: hca quick [options] -- <cmd...>"))
    doctor.add_arguments(sub.add_parser("doctor", help="check the environment for things that corrupt measurements"))
    init = sub.add_parser("init", help="validate hca.toml, create the branch, run the baseline A/A (part B)")
    init.add_argument("--heldout", help="directory of held-out data, copied to the referee store")
    init.add_argument("--force", action="store_true", help="finish even if the baseline is too noisy")
    init.add_argument("--reset", action="store_true", help="drop arm worktrees and start over")
    try_ = sub.add_parser("try", help="measure the current edits against the last keep (part B)")
    try_.add_argument("-m", "--message", help="what changed and why")
    sub.add_parser("confirm", help="re-measure the cumulative change on held-out data (part B)")
    report = sub.add_parser("report", help="print the execution report (part B)")
    report.add_argument("--md", help="also write the report to this markdown path")
    sub.add_parser("status", help="print the short state (part B)")
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
        print(f"hca {args.cmd}: {NOT_YET}", file=sys.stderr)
        return EXIT_USAGE
    except HcaError as exc:
        print(f"hca: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("hca: interrupted", file=sys.stderr)
        return 130
