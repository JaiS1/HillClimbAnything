"""`hca report [--md PATH]` and `hca status`: read the ledger and say what is true, nothing more.

The headline is the confirmed delta against the original (if a confirm has passed), then the
unconfirmed cumulative. The attempts table includes the invalid ones, because a validity rate that
hides them would flatter the run. Reverts whose interval still allowed the useful effect are called
out: no evidence is not no effect. It also says how many false keeps chance alone allows so far
(tries x 1%), and `--referee` shows a human what the referee store holds (the confirm detail the agent is
never shown, the reset log).
"""

from __future__ import annotations

from pathlib import Path

from .. import explore, gitops, ledger
from ..core import HcaError
from ..spec import load_spec


def add_report_arguments(parser) -> None:
    parser.add_argument("--md", help="also write the report as markdown to this path")
    parser.add_argument("--referee", action="store_true",
                        help="show the referee store's detail (confirm numbers, reset log). For the human, not the agent")
    parser.add_argument("--dream", action="store_true",
                        help="write .hca/DREAM.md: an offline packet of the archive, lineages and open evidence to write new ideas from")


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


def _explore_lines(root, state, tests, screens, counts) -> list:
    """Explore mode's disclosure lines (none in a line-mode climb): screens (valid and invalid) kept out of the bound,
    promotions, the override rate, stepping stones, dropped keeps and tainted promotions. Counts, stones and dropped keeps
    are derived from the referee store and the refs, so a ledger written by an older hca reads correctly too."""
    if not (counts["screen"] or counts["promote"]):
        return []
    task = state["task"]
    by_n = explore.entries(task)
    valid = sum(1 for e in by_n.values() if ledger.kind_of(e) == "screen" and e.get("valid"))
    promos = [a for a in tests if a.get("kind") == "promote"]
    overrides = [a for a in screens if ((a.get("explore") or {}).get("sampling") or {}).get("parent_p", 0) is None]
    divergent = sum(1 for a in screens if ((a.get("explore") or {}).get("divergence") or 0) > 0.25)
    stones = [n for n, e in by_n.items() if ledger.kind_of(e) == "screen" and e.get("valid") and (e.get("estimate_log") or 0) < 0]
    rate = f" ({100 * len(overrides) / len(screens):.0f}% of {len(screens)} screens)" if screens else ""
    lines = [f"Screens: {counts['screen']} ({valid} valid, {counts['screen'] - valid} invalid); they rank and archive a child against "
             "its parent, cannot keep, and are not in the bound above.",
             f"Promotions: {counts['promote']} ({sum(1 for a in promos if a.get('verdict') == 'keep')} kept); parent overrides "
             f"{len(overrides)}{rate}; high-divergence children (> 25% of the parent's lines) {divergent}.",
             f"Stepping stones: {len(stones)} screen(s) read worse than their own parent; kept promotions built on one: "
             f"{len(explore.stone_keeps(task))}."]
    for a in promos:
        dropped = explore.dropped_at(root, state, a["n"])
        if dropped:
            lines.append(f"Promotion {a['n']:04d} dropped " + ", ".join(f"keep {k:04d}" for k in dropped)
                         + " (the member lacks them; its whole tree was tested).")
        if (a.get("explore") or {}).get("tainted"):
            lines.append(f"Promotion {a['n']:04d} promoted a TAINTED member (--tainted): its lineage held a keep a failed confirm undid.")
    return lines


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
    screens = [a for a in attempts if a.get("kind") == "screen"]
    attempts = ledger.tests_only(attempts)               # the table, validity and headline are about referee tests (N2)
    head = ledger.headline(state, attempts)
    valid = sum(1 for a in attempts if a.get("valid"))
    total = len(attempts)
    rows = _rows(attempts, mode)
    unproven = [a for a in attempts if a.get("verdict") == "revert" and a.get("valid") and a.get("includes_useful")]
    mech = _mechanism(attempts, spec.mechanism.metrics) if spec else []
    sliced = next((c for c in reversed(confirms) if c.get("slices")), None)
    tries = ledger.tries_used(root, state["task"])
    counts = ledger.store_counts(state["task"])
    tip = state.get("last_confirmed") or state["last_keep"]
    stat = gitops.diff_stat(root, state["init_sha"], tip) if tip != state["init_sha"] else ""
    return {"mode": mode, "headline": head, "rows": rows, "valid": valid, "total": total, "unproven": unproven,
            "mechanism": mech, "slices": sliced, "diffstat": stat, "tip": tip, "tries": max(tries + counts["promote"], total),
            "explore": _explore_lines(root, state, attempts, screens, counts), "chain": ledger.chain_of(state["task"]),
            "resets": (ledger.load_store(state["task"]) or {}).get("resets", [])}


def render_text(state, spec, data) -> str:
    mode = data["mode"]
    widths = [max(len(HEADER[i]), *(len(r[i]) for r in data["rows"])) if data["rows"] else len(HEADER[i]) for i in range(len(HEADER))]

    def line(cells):
        return "  ".join(str(c).ljust(widths[i]) if i < len(cells) - 1 else str(c) for i, c in enumerate(cells))

    useful = spec.objective.useful_effect if spec else state.get("useful_effect")
    renamed = f"; run with `init --name`, hca.toml says {state['spec_task']!r}" if state.get("spec_task") else ""
    lines = [f"HCA report: {state['task']} (branch {state['branch']}{renamed}; {mode} mode; {state.get('metric')} {state.get('direction')}; "
             f"useful effect {useful})", "", "Headline", "  " + data["headline"]["text"]] + _chain_lines(data["chain"]) + ["", "Attempts"]
    if data["rows"]:
        lines.append("  " + line(HEADER))
        lines += ["  " + line(r) for r in data["rows"]]
    else:
        lines.append("  none yet")
    pct = 100 * data["valid"] / data["total"] if data["total"] else 0
    lines += ["", f"Validity rate: {data['valid']} of {data['total']} tries valid ({pct:.0f}%). "
              "Invalid = scope violation, no change, build/guard failure, crash, or an unfinished measurement."]
    lines += ["", expected_false_keeps(data["tries"], bool(data["explore"]))] + data["explore"]
    if data["resets"]:
        lines += [f"The baseline was re-initialised {len(data['resets'])} time(s) (see `hca report --referee`): the original is the latest one."]
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
    earlier = data["chain"]["confirms"]
    lines += ["", f"Confirms used: {ledger.confirms_used(state, state['task']) + earlier} of {spec.limits.confirms if spec else '?'}"
              + (f" ({earlier} under earlier names of this climb)." if earlier else ".")]
    if data["diffstat"]:
        lines += ["", f"Change so far ({'confirmed' if state.get('last_confirmed') == data['tip'] else 'latest keep'} {data['tip'][:12]} vs original):",
                  *["  " + l for l in data["diffstat"].splitlines()]]
    base = state.get("base_branch")
    lines += ["", "To take the result: " + (f"git checkout {base or '<your main branch>'} && ") + f"git merge {state['branch']}"
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
    out += ["", f"**Validity rate:** {data['valid']} of {data['total']} tries valid ({pct:.0f}%).", "",
            expected_false_keeps(data["tries"], bool(data["explore"]))] + [x for line in data["explore"] for x in ("", line)]
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
    out += ["", "## Merge", "", "```", f"git checkout {base or '<your main branch>'}\n" + f"git merge {state['branch']}", "```"]
    return "\n".join(out) + "\n"


def _chain_lines(chain) -> list:
    """The headline's re-baseline line: a renamed climb (`init --name`) always says what came before it."""
    if not chain["tasks"]:
        return []
    names = " -> ".join(t["task"] for t in chain["tasks"])
    return [f"  Re-baselined from {chain['tasks'][-1]['task']!r} after {chain['confirms']} confirm(s) ({chain['fails']} FAIL) along "
            f"the chain {names}; this headline is measured from the new baseline only."]


def expected_false_keeps(tries, promotions=False) -> str:
    """The family-wise honesty line: each try (and each promotion) is a 1% test, so chance alone allows about n x 1% false keeps."""
    return (f"Expected false keeps so far <= {tries} {'tries + promotions' if promotions else 'tries'} x 1% = {tries * 0.01:.2f} "
            "(an upper bound from chance alone; `hca confirm` is the check).")


def render_referee(task, root) -> str:
    """What the referee store holds, for a human: the store record, every confirm in full (minus per-item and per-block arrays), resets."""
    store = ledger.load_store(task)
    lines = [f"Referee store for {task}: {ledger.store_dir(task)}", ""]
    if store is None:
        return "\n".join(lines + ["No record."]) + "\n"
    lines += [f"original {str(store.get('init_sha'))[:12]}, confirms used {store.get('confirms_used', 0)}, "
              f"attempts recorded {len(store.get('attempts', []))}, frozen files {len(store.get('frozen', {}))}"]
    for r in store.get("resets", []):
        lines.append(f"reset at {r.get('at')}: {str(r.get('previous_init_sha'))[:12]} -> {str(r.get('new_init_sha'))[:12]} "
                     f"(confirms used then: {r.get('confirms_used')}; HCA_ALLOW_RESET set: {r.get('allowed_by_env')})")
    for r in store.get("renamed_from", []) + store.get("renamed_to", []):
        lines.append(f"rename at {r.get('at')}: hca.toml task {r.get('spec_name')!r} climbed as {r.get('task')!r} from "
                     f"{str(r.get('init_sha'))[:12]} (the hca.toml name had used {r.get('spec_name_confirms_used')} confirms)")
    lines += ["", "Confirms"]
    confirms = ledger.read_confirms(task)
    if not confirms:
        lines.append("  none")
    for c in confirms:
        bulky = {k: v for k, v in c.items() if k not in ("scores", "per_item", "arm_medians", "slices")}
        lines.append("  " + ", ".join(f"{k}={v}" for k, v in bulky.items()))
    lines.append("")
    lines.append(f"Per-block scores, per-item values and slices are in {ledger.store_dir(task) / 'confirms.jsonl'}.")
    return "\n".join(lines) + "\n"


def run(args, out=print, cwd=None) -> int:
    root, spec, state = _context(cwd)
    if getattr(args, "referee", False):
        out(render_referee(state["task"], root).rstrip("\n"))
        return 0
    if getattr(args, "dream", False):
        from .next_ import dream
        if spec is None:
            raise HcaError("report --dream needs a valid hca.toml. Fix: restore it (it is frozen).")
        with ledger.lock(root, "report --dream", out):  # it rewrites .hca/archive.json and DREAM.md: never beside a running try
            path = dream(root, spec, state)
        out(f"Wrote {path.relative_to(root).as_posix()}: read it, then write new ideas into IDEAS.md. No benchmark was run.")
        return 0
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
    attempts = ledger.tests_only(attempts)
    out(f"task {state['task']}  branch {state['branch']}" + ("" if here == state["branch"] else f"  (you are on {here})"))
    out(f"init {state['init_sha'][:12]}  last keep {state['last_keep'][:12]}  last confirmed "
        + (state["last_confirmed"][:12] if state.get("last_confirmed") else "none"))
    out(f"tries {max(len(attempts), ledger.tries_used(root, state['task']))} / {max_tries}  (kept {kept}, valid {sum(1 for a in attempts if a.get('valid'))})   "
        f"confirms {ledger.confirms_used(state, state['task'])} / {max_confirms}")
    out(ledger.headline(state, attempts)["text"])
    out("(`hca status` is kept for one release; `hca next` shows this and what to do next.)")
    return 0
