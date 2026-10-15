"""`hca report [--md PATH]` and `hca status`: read the ledger and say what is true, nothing more.

The headline is the confirmed delta against the original (if a confirm has passed), then the
unconfirmed cumulative. The attempts table includes the invalid ones, because a validity rate that
hides them would flatter the run. Reverts whose interval still allowed the useful effect are called
out: no evidence is not no effect.
"""

from __future__ import annotations

from pathlib import Path

from .. import gitops, ledger
from ..core import HcaError
from ..spec import load_spec


def add_report_arguments(parser) -> None:
    parser.add_argument("--md", help="also write the report as markdown to this path")


def add_status_arguments(parser) -> None:
    pass


def _context(cwd):
    root = gitops.repo_root(cwd)
    state = ledger.load_state(root)
    try:
        spec = load_spec(root / "hca.toml")
    except HcaError:
        spec = None
    return root, spec, state


def _cell(value, mode, kind="effect") -> str:
    if value is None:
        return "-"
    return ledger.fmt_effect(value, mode) if kind == "effect" else f"{value:.3g}"


def _rows(attempts, mode) -> list:
    rows = []
    for a in attempts:
        verdict = a["verdict"] + ({"unconfirmed": " (unconfirmed)", "confirmed": " (confirmed)"}.get(a.get("confirm") or "", ""))
        blocks = a.get("blocks") if a.get("blocks") else "-"
        if a.get("extended"):
            blocks = f"{blocks} (ext)"
        rows.append([f"{a['n']:04d}", verdict, _cell(a.get("estimate"), mode), _cell(a.get("p"), mode, "p"), str(blocks),
                     f"{a.get('wall_seconds', 0):.1f}s", (a.get("message") or "").splitlines()[0][:70] if a.get("message") else ""])
    return rows


HEADER = ["n", "verdict", "estimate", "p", "blocks", "wall", "message"]


def _mechanism(attempts, names) -> list:
    out = []
    for a in attempts:
        if a.get("verdict") != "keep" or not a.get("arm_medians"):
            continue
        parts = []
        for name in names:
            base, cand = a["arm_medians"]["baseline"].get(name), a["arm_medians"]["candidate"].get(name)
            if base is None or cand is None:
                continue
            change = f" ({(cand - base) / base * 100:+.1f}%)" if base else ""
            parts.append(f"{name} {base:.4g} -> {cand:.4g}{change}")
        if parts:
            out.append(f"{a['n']:04d}: " + "; ".join(parts))
    return out


def build(root, spec, state, attempts, confirms) -> dict:
    mode = state.get("mode", "blocks")
    head = ledger.headline(state, attempts)
    valid = sum(1 for a in attempts if a.get("valid"))
    total = len(attempts)
    rows = _rows(attempts, mode)
    unproven = [a for a in attempts if a.get("verdict") == "revert" and a.get("valid") and a.get("includes_useful")]
    mech = _mechanism(attempts, spec.mechanism.metrics) if spec else []
    sliced = next((c for c in reversed(confirms) if c.get("slices")), None)
    tip = state.get("last_confirmed") or state["last_keep"]
    stat = gitops.diff_stat(root, state["init_sha"], tip) if tip != state["init_sha"] else ""
    return {"mode": mode, "headline": head, "rows": rows, "valid": valid, "total": total, "unproven": unproven,
            "mechanism": mech, "slices": sliced, "diffstat": stat, "tip": tip}


def render_text(state, spec, data) -> str:
    mode = data["mode"]
    widths = [max(len(HEADER[i]), *(len(r[i]) for r in data["rows"])) if data["rows"] else len(HEADER[i]) for i in range(len(HEADER))]

    def line(cells):
        return "  ".join(str(c).ljust(widths[i]) if i < len(cells) - 1 else str(c) for i, c in enumerate(cells))

    useful = spec.objective.useful_effect if spec else state.get("useful_effect")
    lines = [f"HCA report: {state['task']} (branch {state['branch']}; {mode} mode; {state.get('metric')} {state.get('direction')}; "
             f"useful effect {useful})", "", "Headline", "  " + data["headline"]["text"], "", "Attempts"]
    if data["rows"]:
        lines.append("  " + line(HEADER))
        lines += ["  " + line(r) for r in data["rows"]]
    else:
        lines.append("  none yet")
    pct = 100 * data["valid"] / data["total"] if data["total"] else 0
    lines += ["", f"Validity rate: {data['valid']} of {data['total']} tries valid ({pct:.0f}%). "
              "Invalid = scope violation, no change, build/guard failure, crash, or an unfinished measurement."]
    if data["mechanism"]:
        lines += ["", "Mechanism metrics (kept attempts, baseline -> candidate medians)"] + ["  " + m for m in data["mechanism"]]
    if data["slices"]:
        c = data["slices"]
        lines += ["", f"Slices from confirm {c['n']} (median item change per tag value, the metric's own %)"]
        for name, s in sorted(c["slices"].items()):
            lines.append(f"  {name}: {_cell(s['median_pct'], 'blocks')} over {s['items']} item(s)" + (f" ({s['skipped']} skipped)" if s["skipped"] else ""))
    if data["unproven"]:
        ids = ", ".join(f"{a['n']:04d}" for a in data["unproven"])
        lines += ["", f"No evidence is not no effect: {len(data['unproven'])} reverted attempt(s) ({ids}) have an interval that still allows "
                  f"the useful effect ({useful}). They were not shown to fail, only not shown to work."]
    lines += ["", f"Confirms used: {ledger.confirms_used(state, state['task'])} of {spec.limits.confirms if spec else '?'}."]
    if data["diffstat"]:
        lines += ["", f"Change so far ({'confirmed' if state.get('last_confirmed') == data['tip'] else 'latest keep'} {data['tip'][:12]} vs original):",
                  *["  " + l for l in data["diffstat"].splitlines()]]
    base = state.get("base_branch")
    lines += ["", "To take the result: " + (f"git checkout {base} && " if base else "") + f"git merge {state['branch']}"
              + ("   (only the confirmed commit: git merge " + state["last_confirmed"][:12] + ")" if state.get("last_confirmed")
                 and state["last_confirmed"] != state["last_keep"] else "")]
    return "\n".join(lines) + "\n"


def render_markdown(state, spec, data) -> str:
    mode = data["mode"]
    useful = spec.objective.useful_effect if spec else state.get("useful_effect")
    out = [f"# HCA report: {state['task']}", "",
           f"Branch `{state['branch']}`, {mode} mode, metric `{state.get('metric')}` ({state.get('direction')}), useful effect {useful}.", "",
           "## Headline", "", data["headline"]["text"], "", "## Attempts", ""]
    if data["rows"]:
        out.append("| " + " | ".join(HEADER) + " |")
        out.append("|" + "|".join("---" for _ in HEADER) + "|")
        out += ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in data["rows"]]
    else:
        out.append("None yet.")
    pct = 100 * data["valid"] / data["total"] if data["total"] else 0
    out += ["", f"**Validity rate:** {data['valid']} of {data['total']} tries valid ({pct:.0f}%)."]
    if data["mechanism"]:
        out += ["", "## Mechanism metrics", ""] + [f"- {m}" for m in data["mechanism"]]
    if data["slices"]:
        c = data["slices"]
        out += ["", f"## Slices (confirm {c['n']})", ""]
        out += [f"- `{name}`: {_cell(s['median_pct'], 'blocks')} over {s['items']} item(s)" for name, s in sorted(c["slices"].items())]
    if data["unproven"]:
        out += ["", "## No evidence is not no effect", "",
                f"{len(data['unproven'])} reverted attempt(s) ({', '.join(format(a['n'], '04d') for a in data['unproven'])}) have an interval that "
                f"still allows the useful effect ({useful})."]
    if data["diffstat"]:
        out += ["", "## Change so far", "", "```", data["diffstat"], "```"]
    base = state.get("base_branch")
    out += ["", "## Merge", "", "```", (f"git checkout {base}\n" if base else "") + f"git merge {state['branch']}", "```"]
    return "\n".join(out) + "\n"


def run(args, out=print, cwd=None) -> int:
    root, spec, state = _context(cwd)
    attempts = ledger.read_attempts(root)
    data = build(root, spec, state, attempts, ledger.read_confirms(state["task"]))
    out(render_text(state, spec, data).rstrip("\n"))
    if getattr(args, "md", None):
        path = Path(args.md)
        path.write_text(render_markdown(state, spec, data), encoding="utf-8")
        out(f"Wrote {path}.")
    return 0


def run_status(args=None, out=print, cwd=None) -> int:
    root, spec, state = _context(cwd)
    attempts = ledger.read_attempts(root)
    mode = state.get("mode", "blocks")
    kept = sum(1 for a in attempts if a.get("verdict") == "keep" and a.get("confirm") != "unconfirmed")
    max_tries = spec.objective.max_tries if spec else "?"
    max_confirms = spec.limits.confirms if spec else "?"
    here = gitops.current_branch(root)
    out(f"task {state['task']}  branch {state['branch']}" + ("" if here == state["branch"] else f"  (you are on {here})"))
    out(f"init {state['init_sha'][:12]}  last keep {state['last_keep'][:12]}  last confirmed "
        + (state["last_confirmed"][:12] if state.get("last_confirmed") else "none"))
    out(f"tries {len(attempts)} / {max_tries}  (kept {kept}, valid {sum(1 for a in attempts if a.get('valid'))})   "
        f"confirms {ledger.confirms_used(state, state['task'])} / {max_confirms}")
    out(ledger.headline(state, attempts)["text"])
    return 0
