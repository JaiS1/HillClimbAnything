"""`hca next [--parent M]`: what to do next.

`next` replaces `status` (which stays as the old short view for one release). It prints one screen of at most
60 lines however long the climb: the budget (counted from the referee store), the parent the working tree is at and
its lineage, siblings, two inspirations from other modules, the ideas in IDEAS.md with their outcome classes, the
archive, the promotion hint, warnings, and a backlog in a fixed mechanical order. With `--parent M` it sets the
editable paths to member M first (refused over unscreened edits).

It never shows confirm detail beyond PASS/FAIL and the passed estimate, or anything held out. Kept estimates are marked as selected (biased upward); only the confirm line is a result.
"""

from __future__ import annotations

import math
import re

from .. import explore, gitops, ledger, stats
from ..core import Refusal
from . import common

MAX_LINES = 60
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ")


def add_arguments(parser) -> None:
    parser.add_argument("--parent", help="set the editable paths to archive member M (an attempt id) and build the next child from it")


def run(args=None, out=print, cwd=None) -> int:
    root = gitops.repo_root(cwd)
    with ledger.lock(root, "next", out):
        root, spec, state = common.open_session(cwd, out=out)
        _choose_parent(root, spec, state, getattr(args, "parent", None))
        for line in render(root, spec, state)[:MAX_LINES]:
            out(line)
    return 0


def _choose_parent(root, spec, state, wanted) -> None:
    """`--parent M` (an override, logged with parent_p null), or a fresh draw when explore mode has no parent decided yet."""
    _, here = explore.parent_base(root, state)
    clean = gitops.working_tree_hash(root, spec, here) == gitops.tree_of(root, here)
    if wanted is None and (state.get("explore") is None or state["explore"].get("sampling") is not None or not clean):
        return
    if not clean:
        raise Refusal("the editable paths hold edits that were never screened or tried; moving them to another parent would lose them. "
                      "Fix: screen them (`hca try --explore`) or undo them first.")
    archive = explore.build(root, spec, state, explore.climb_seed(state))
    if wanted is None:
        sampling = explore.sample(archive, ledger.store_counts(state["task"])["screen"], archive["seed"])
        parent = None if sampling["parent"] == archive["incumbent"] else sampling["parent"]
    else:
        parent = None if str(wanted) in ("incumbent", "0", str(archive["incumbent"])) else int(wanted)
        sampling = {"sampler": explore.SAMPLER, "override": "next --parent", "parent": parent, "parent_p": None}
    target = state["last_keep"] if parent is None else explore.member(root, state, parent, what="parent")["sha"]
    gitops.revert(root, spec, target)
    state["explore"] = {"parent": parent, "sampling": sampling}
    ledger.save_state(root, state)


# --- shared reading ---------------------------------------------------------------------------------

def _context(root, spec, state) -> dict:
    task = state["task"]
    records = {r["n"]: r for r in ledger.read_attempts(root) if isinstance(r.get("n"), int)}
    entries = explore.entries(task)
    archive = explore.build(root, spec, state, explore.climb_seed(state))
    explore.write_cache(root, archive)
    useful, direction = spec.objective.useful, spec.objective.direction
    for n, r in records.items():                       # the class is derived from the store's kind, not the record's own label
        r["kind"] = ledger.kind_of(entries.get(n, {})) if n in entries else r.get("kind", "try")
        r["class"] = explore.outcome_class(r, useful, direction)
    n_s = explore.screen_blocks(state, spec)
    sigma = (state.get("noise") or {}).get("noise_sd") or 0.0
    return {"task": task, "records": records, "entries": entries, "archive": archive, "n_s": n_s,
            "resolution": 2 * sigma / math.sqrt(n_s), "counts": ledger.store_counts(task), "ideas": explore.read_ideas(root),
            "wall": sum(e.get("wall_seconds") or 0 for e in entries.values()), "direction": direction}


def _pct(log_value, direction) -> str:
    return ledger.fmt_effect(stats.metric_change_pct(log_value, direction), "blocks")


def _idea_outcomes(ctx) -> dict:
    """{idea id: the latest record that named it}."""
    latest = {}
    for n in sorted(ctx["records"]):
        if ctx["records"][n].get("idea"):
            latest[ctx["records"][n]["idea"]] = ctx["records"][n]
    return latest


def _hunks(patch) -> dict:
    """{path: [(start, end)]} of the old-side line ranges a unified diff touches."""
    out, path = {}, None
    for line in patch.splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("+++ ") and path is None:
            path = line[6:] if line.startswith("+++ b/") else None
        elif path and _HUNK.match(line):
            m = _HUNK.match(line)
            start = int(m.group(1))
            out.setdefault(path, []).append((start, start + max(1, int(m.group(2) or 1))))
    return out


def _patch(root, n) -> str:
    try:
        return (ledger.attempt_path(root, n) / "patch.diff").read_text(encoding="utf-8")
    except OSError:
        return ""


def _overlaps(root, spec, state, ctx) -> list:
    """Earlier reverted or screened attempts whose hunks overlap the working diff (novelty rejection without embeddings)."""
    _, here = explore.parent_base(root, state)
    mine = _hunks(gitops.uncommitted_patch(root, spec, here))
    if not mine:
        return []
    hits = []
    for n, r in sorted(ctx["records"].items(), reverse=True)[:40]:
        if r.get("verdict") not in ("revert", "screened"):
            continue
        theirs = _hunks(_patch(root, n))
        if any(a < d and c < b for p in mine for a, b in mine[p] for c, d in theirs.get(p, [])):
            hits.append(f"{n:04d} {r['class']}")
    return hits


# --- hca next ---------------------------------------------------------------------------------------

def render(root, spec, state) -> list:
    ctx = _context(root, spec, state)
    archive, records, nodes, direction = ctx["archive"], ctx["records"], ctx["archive"]["nodes"], ctx["direction"]
    counts, floor = ctx["counts"], 0.5 * spec.objective.useful
    lines = [f"climb     {ctx['task']} · {ctx['wall'] / 60:.1f} min hca wall · tries {counts['try']} · promotions {counts['promote']} · "
             f"screens {counts['screen']} · confirms {ledger.confirms_used(state, ctx['task'])} of {spec.limits.confirms}",
             f"screens   {ctx['n_s']} blocks vs parent · resolution ≈ {100 * ctx['resolution']:.1f}% · keep floor {100 * floor:.1f}%"]
    confirmed = state.get("last_confirmed_result")
    inc = archive["incumbent"]
    lines.append(f"incumbent {'keep ' + format(inc, '04d') if inc else 'the original'}"
                 + (f" (confirmed {ledger.fmt_effect(confirmed['estimate'], 'blocks')} vs the original"
                    + ("" if state.get("last_confirmed") == state["last_keep"] else "; later keeps unconfirmed") + ")" if confirmed else " (no confirm yet)"))
    explore_state = state.get("explore") or {}
    parent = explore_state.get("parent")
    if parent is not None and parent in nodes:
        node, sampling = nodes[parent], explore_state.get("sampling") or {}
        how = "chosen by override" if sampling.get("parent_p") is None else \
            f"sampled p = {sampling['parent_p']:.2f} (incumbent mass {explore.INCUMBENT_MASS:.2f}, sampler {explore.SAMPLER})"
        lines.append(f"parent    {parent:04d}  [{node.get('cell', node['module'])}]  {how}")
        for i in node["lineage"]:
            r = records.get(i, {})
            step = nodes.get(i)
            est = f"{_pct(r['estimate_log'], direction)} vs parent" if r.get("estimate_log") is not None else ""
            stone = " (stepping stone)" if step and r.get("estimate_log", 0) < 0 else ""
            lines.append(f"          {'lineage ' if i == node['lineage'][0] else '        '} {i:04d} {r.get('idea') or ''} "
                         f"{(r.get('message') or '').splitlines()[0][:50] if r.get('message') else ''}  {est}{stone}".rstrip())
        lines.append(f"          lead vs incumbent  raw {100 * (node['f_raw'] - archive['f_inc']):+.1f} pp · ranked "
                     f"{100 * (node['f_rank'] - archive['f_inc']):+.1f} pp ({node['d']} screened step(s))")
        sibs = [n for n, e in sorted(ctx["entries"].items()) if e.get("parent") == parent and ledger.kind_of(e) == "screen"][-4:]
        if sibs:
            lines.append("siblings  " + " · ".join(f"{n:04d} {records.get(n, {}).get('class', '?')}"
                                                   + (f" {_pct(nodes[n]['f_raw'] - node['f_raw'], direction)}" if n in nodes else "") for n in sibs))
    else:
        p = (explore_state.get("sampling") or {}).get("parent_p", 1.0)
        lines.append("parent    the incumbent" + ("" if not archive["elites"] else " (chosen by override)" if p is None else f" (sampled p = {p:.2f})"))
    here_cell = nodes.get(parent, {}).get("cell")
    inspire = sorted((n for n in archive["elites"] if n != parent and nodes[n].get("cell") != here_cell), key=lambda n: -nodes[n]["f_rank"])[:2]
    for n in inspire:
        lines.append(f"inspire   {n:04d} [{nodes[n]['cell']}] ranked lead {100 * (nodes[n]['f_rank'] - archive['f_inc']):+.1f} pp · "
                     f".hca/attempts/{n:04d}/patch.diff")
    lines += _memory_lines(ctx)
    dropped = [f"keep {k:04d} dropped by {n:04d}" for n, e in sorted(ctx["entries"].items()) for k in e.get("dropped_keeps") or []]
    if dropped:
        lines.append("dropped   " + " · ".join(dropped[-4:]))
    best = explore.hint(archive, ctx["n_s"])
    if best is None:
        lead = max((nodes[n]["f_rank"] - archive["f_inc"] for n in archive["elites"]), default=None)
        lines.append("promote   none" + ("" if lead is None else f": best ranked lead {100 * lead:+.1f} pp < the hint's {100 * floor:.1f} pp floor plus selection allowance"))
    elif best["rescreen"]:
        lines.append(f"promote   {best['n']:04d} ranked lead {100 * best['lead']:+.1f} pp ≥ {100 * best['needed']:.1f} pp, but screens here are noisy: "
                     f"re-screen it first (`hca next --parent {best['n']:04d}`, then `hca try --explore -m \"re-screen\"` with no edits)")
    else:
        lines.append(f"promote   {best['n']:04d} ranked lead {100 * best['lead']:+.1f} pp ≥ {100 * best['needed']:.1f} pp  →  "
                     f"hca try --promote {best['n']:04d} -m \"...\"")
    overlap = _overlaps(root, spec, state, ctx)
    if overlap:
        lines.append("overlap   your working diff touches the same lines as " + ", ".join(overlap[:4]) + ": check it is a new idea")
    lines += _warnings(ctx)
    backlog = _backlog(ctx, parent)
    if backlog:
        lines.append("backlog   " + " · ".join(backlog[:8]))
    lines.append(f"working   editable paths are at {'the incumbent' if parent is None else format(parent, '04d')} "
                 f"(branch at {'keep ' + format(inc, '04d') if inc else 'the original'})")
    lines.append("then      edit, then `hca try --explore --idea I<n> -m \"...\"` (a screen), or plain `hca try -m \"...\"` (a full test)")
    return lines


def _memory_lines(ctx) -> list:
    latest, lines = _idea_outcomes(ctx), []
    kept = [f"{i} {ledger.fmt_effect(r.get('estimate'), 'blocks')}*" for i, r in latest.items() if r["class"] == "KEPT"]
    open_ = [f"{i} {r['class']} {ledger.fmt_effect(r.get('estimate'), 'blocks')} (attempt {r['n']:04d})"
             for i, r in latest.items() if r["class"] in ("NO EVIDENCE", "SMALL", "SCREENED")]
    closed = [f"{i} {r['class']}" for i, r in latest.items() if r["class"] in ("NULL", "HARMFUL")]
    if kept:
        lines.append("kept      " + " · ".join(kept[-6:]) + "   (* selected, biased upward)")
    if open_:
        lines.append("open      " + " · ".join(open_[-4:]))
    if closed:
        lines.append("closed    " + " · ".join(closed[-6:]))
    kinds = {}
    for r in ctx["records"].values():
        if r.get("idea_kind"):
            kinds[r["idea_kind"]] = kinds.get(r["idea_kind"], 0) + 1
    if kinds:
        lines.append("kinds     " + " · ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
    a = ctx["archive"]
    lines.append(f"archive   {len(a['elites']) + 1} members, {len(a['cells'])} of {explore.MAX_CELLS + 1} modules · "
                 f"{len(a['stepping_stones'])} stepping stones · {len(a['tainted'])} tainted")
    return lines


def _warnings(ctx) -> list:
    entries = sorted(ctx["entries"].items())
    last_promote = max((n for n, e in entries if ledger.kind_of(e) == "promote"), default=0)
    since_promote = sum(1 for n, e in entries if ledger.kind_of(e) == "screen" and n > last_promote)
    confirms = ledger.read_confirms(ctx["task"])
    last_confirm = confirms[-1].get("at", "") if confirms else ""
    since_confirm = sum(1 for _, e in entries if ledger.kind_of(e) == "screen" and e.get("at", "") > last_confirm)
    out = []
    if since_promote >= 10:
        out.append(f"warning   {since_promote} screens since the last promotion attempt: screens are not evidence; promote or change course")
    if since_confirm >= 20:
        out.append(f"warning   {since_confirm} screens since the last confirm: every screen tunes against the dev data; run `hca confirm`")
    return out


def _backlog(ctx, parent) -> list:
    """Untested and still-open ideas in a fixed order: naming the current parent; NO EVIDENCE with room; a `where:` module with no
    archive member; the rest; ideas of a kind with two NULLs go last."""
    latest = _idea_outcomes(ctx)
    nulls = {}
    for r in latest.values():
        if r["class"] == "NULL" and r.get("idea_kind"):
            nulls[r["idea_kind"]] = nulls.get(r["idea_kind"], 0) + 1
    cells = set(ctx["archive"]["cells"])

    def rank(idea):
        r = latest.get(idea["id"])
        if nulls.get(idea["kind"], 0) >= 2:
            return 4
        if parent is not None and idea["parent"] == parent:
            return 0
        if r and r["class"] == "NO EVIDENCE":
            return 1
        if idea["where"] and "/".join(idea["where"].split("/")[:2]) not in cells:
            return 2
        return 3
    todo = [i for i in ctx["ideas"] if latest.get(i["id"], {}).get("class") not in ("KEPT", "NULL", "HARMFUL")]
    return [f"{i['id']} [{i['kind']}]" for i in sorted(todo, key=rank)]
