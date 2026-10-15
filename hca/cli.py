"""The `hca` command line. Subcommands are imported lazily so `hca --help` stays instant."""

from __future__ import annotations

import argparse
import signal
import sys
import time
import traceback

from . import __version__
from .core import HcaError, Refusal

EXIT_USAGE = 3
EXIT_REFUSED = 2
EXIT_INTERNAL = 4         # an unexpected exception: outside the 0 keep/pass, 1 revert/fail, 2 invalid/void, 3 usage contract


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
    previous = _install_signal_handlers()
    try:
        return _dispatch(args, parser)
    except Refusal as exc:
        print(f"hca: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except HcaError as exc:
        print(f"hca: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except _Terminated as exc:
        print("hca: interrupted", file=sys.stderr)
        return 128 + exc.signum
    except KeyboardInterrupt:
        print("hca: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:                  # noqa: BLE001 - the last line of defence: never look like a normal revert (exit 1)
        return _internal_error(exc)
    finally:
        _restore_signal_handlers(previous)


def _dispatch(args, parser) -> int:
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


class _Terminated(KeyboardInterrupt):
    """SIGTERM or SIGHUP, turned into the same unwinding path as Ctrl-C so cleanup and the attempt record run."""

    def __init__(self, signum):
        super().__init__(f"signal {signum}")
        self.signum = signum


def _install_signal_handlers() -> dict:
    """Make SIGTERM and SIGHUP raise KeyboardInterrupt-like errors (an agent tool's timeout sends SIGTERM). Returns the old handlers."""
    def handler(signum, _frame):
        raise _Terminated(signum)

    previous = {}
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):        # not the main thread: nothing to install
            pass
    return previous


def _restore_signal_handlers(previous) -> None:
    for sig, old in previous.items():
        try:
            signal.signal(sig, old)
        except (ValueError, OSError, TypeError):
            pass


def _internal_error(exc) -> int:
    """Print a one-line internal error, write the traceback to .hca/crash-<time>.log when there is a repo, exit 4."""
    text = traceback.format_exc()
    where = None
    try:
        from . import gitops
        directory = gitops.repo_root() / gitops.HCA_DIR
        directory.mkdir(parents=True, exist_ok=True)
        where = directory / f"crash-{time.strftime('%Y%m%dT%H%M%S')}.log"
        where.write_text(text, encoding="utf-8")
    except Exception:                         # noqa: BLE001 - failing to write the log must not hide the error
        where = None
    shown = f"traceback in {where}" if where else "no repo to write a crash log in; traceback follows"
    print(f"hca: internal error: {type(exc).__name__}: {exc} ({shown})", file=sys.stderr)
    if where is None:
        print(text, file=sys.stderr)
    return EXIT_INTERNAL
