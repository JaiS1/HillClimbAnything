"""`hca next [--parent M]` and `hca report --dream`: what to do next, and an offline packet to think with.

`next` replaces `status` (which stays as the old short view for one release). It prints one screen of at most
60 lines however long the climb: the budget (counted from the referee store), the parent the working tree is at and
its lineage, siblings, two inspirations from other modules, the ideas in IDEAS.md with their outcome classes, the
archive, the promotion hint, warnings, and a backlog in a fixed mechanical order. With `--parent M` it sets the
editable paths to member M first (refused over unscreened edits).

`report --dream` writes `.hca/DREAM.md` from the same records with no benchmark running: the agent reads it between
sessions and writes new ideas. Neither ever shows confirm detail beyond PASS/FAIL and the passed estimate, or
anything held out. Kept estimates are marked as selected (biased upward); only the confirm line is a result.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .. import explore, gitops, ledger, stats
from ..core import HcaError, Refusal
from . import common

MAX_LINES = 60
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ")


def add_arguments(parser) -> None:
    parser.add_argument("--parent", help="override the sampler: set the editable paths to archive member M (an attempt id) or `incumbent`")
    parser.add_argument("--why", help="the one-line reason for --parent (required with it; recorded, and the report counts overrides)")


def run(args=None, out=print, cwd=None) -> int:
    root = gitops.repo_root(cwd)
    with ledger.lock(root, "next", out):
        root, spec, state = common.open_session(cwd, out=out)
        gitops.ignore_notebook(True)
        _choose_parent(root, spec, state, getattr(args, "parent", None), getattr(args, "why", None))
        for line in render(root, spec, state)[:MAX_LINES]:
            out(line)
    return 0


WHY_LIMIT = 200


def _clean_why(why) -> str:
    """One printable line of at most 200 characters: control characters dropped, whitespace (newlines too) collapsed."""
    text = " ".join("".join(c if c.isprintable() else " " for c in str(why)).split())
    return text[:WHY_LIMIT] + ("..." if len(text) > WHY_LIMIT else "")


def _choose_parent(root, spec, state, wanted, why=None) -> None:
    """`--parent M --why "..."` (an override, logged with parent_p null and the reason), or a fresh draw when explore mode has
    no parent decided yet: the default flow, where `next` alone sets the parent and prints the command to run."""
    if wanted is not None:
        wanted = str(wanted).strip()
        if wanted != "incumbent" and not wanted.isdigit():
            raise HcaError(f"--parent takes an attempt id (such as 0024) or `incumbent`, not {wanted!r}. Fix: see `hca next` for the members.")
        why = _clean_why(why or "")
        if not why:
            raise HcaError(f'--parent overrides the sampler, so it needs a one-line reason: `hca next --parent {wanted} --why "..."`. '
                           "The reason is recorded with the next screen, and the report counts overrides.")
    parent_now, here = explore.parent_base(root, state)
    clean = gitops.working_tree_hash(root, spec, here) == gitops.tree_of(root, here)
    if wanted is None and (state.get("explore") is None or state["explore"].get("sampling") is not None or not clean):
        return
    if not clean:
        paths = sorted(gitops.numstat(root, spec, here, gitops.working_tree_hash(root, spec, here))) or ["<the editable paths>"]
        label = "the incumbent" if parent_now is None else f"member {parent_now:04d}"
        raise Refusal(f"the editable paths hold edits that were never screened or tried ({', '.join(paths[:5])}"
                      f"{' ...' if len(paths) > 5 else ''}); moving to another parent would lose them. Recover: screen them first "
                      f'(`hca try --explore -m "..."`), or drop them with `git checkout {here[:12]} -- {" ".join(paths[:5])}` (back to '
                      f"{label}, the parent hca recorded), then run this again. Do not `git stash` them: a stash puts the files back to "
                      f"the branch tip (the last keep), not to {label}, so the next screen would measure undoing the parent.")
    archive = explore.build(root, spec, state, explore.climb_seed(state))
    if wanted is None:
        sampling = explore.sample(archive, ledger.store_counts(state["task"])["screen"], archive["seed"])
        parent = None if sampling["parent"] == archive["incumbent"] else sampling["parent"]
    else:
        parent = None if wanted == "incumbent" or int(wanted) in (0, archive["incumbent"]) else int(wanted)
        sampling = {"sampler": explore.SAMPLER, "override": "next --parent", "parent": parent, "parent_p": None, "why": why}
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
    counts = ledger.store_counts(task)
    return {"task": task, "records": records, "entries": entries, "archive": archive, "n_s": n_s,
            "resolution": 2 * sigma / math.sqrt(n_s), "counts": counts, "ideas": explore.read_ideas(root),
            "valid_screens": archive["screens"], "invalid_screens": counts["screen"] - archive["screens"],
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
    invalid = f" ({ctx['invalid_screens']} invalid)" if ctx["invalid_screens"] else ""
    lines = [f"climb     {ctx['task']} · {ctx['wall'] / 60:.1f} min hca wall · tries {counts['try']} · promotions {counts['promote']} · "
             f"screens {ctx['valid_screens']}{invalid} · confirms {ledger.confirms_used(state, ctx['task']) + ledger.chain_of(ctx['task'])['confirms']} "
             f"of {spec.limits.confirms}",
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
        how = f"chosen by override ({sampling.get('why') or sampling.get('override') or 'no reason given'})" if sampling.get("parent_p") is None else \
            f"sampled p = {sampling['parent_p']:.2f} (incumbent mass {explore.INCUMBENT_MASS:.2f}, sampler {explore.SAMPLER})"
        lines.append(f"parent    {parent:04d}  [{node.get('cell', node['module'])}]  {how}")
        for i in node["lineage"]:
            r = records.get(i, {})
            step = nodes.get(i)
            est = f"{_pct(r['estimate_log'], direction)} vs parent" if r.get("estimate_log") is not None else ""
            stone = " (stepping stone: read worse than its parent)" if step and step["screened"] and step["step"] < 0 else ""
            stone += " (in the incumbent)" if step and step["retired"] else ""
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
    promos = [n for n, e in sorted(ctx["entries"].items()) if ledger.kind_of(e) == "promote"][-4:]
    dropped = [f"keep {k:04d} dropped by {n:04d}" for n in promos for k in explore.dropped_at(root, state, n)]
    if dropped:
        lines.append("dropped   " + " · ".join(dropped[-4:]))
    lines += _hint_lines(archive, ctx, floor)
    pending = sum(1 for r in records.values() if r.get("kind") != "screen" and r.get("verdict") == "keep" and r.get("confirm") == "pending")
    if pending >= 2:
        left = spec.limits.confirms - ledger.confirms_used(state, ctx["task"]) - ledger.chain_of(ctx["task"])["confirms"]
        lines.append(f"confirm   {pending} keeps since the last confirm  →  hca confirm ({left} of {spec.limits.confirms} left): "
                     "only a confirm is a result, and a FAIL then undoes less")
    overlap = _overlaps(root, spec, state, ctx)
    if overlap:
        lines.append("overlap   your working diff touches the same lines as " + ", ".join(overlap[:4]) + ": check it is a new idea")
    lines += _warnings(ctx)
    backlog = _backlog(ctx, parent)
    if backlog:
        lines.append("backlog   " + " · ".join(backlog[:8]))
    lines.append(f"working   editable paths are at {'the incumbent' if parent is None else format(parent, '04d')} "
                 f"(branch at {'keep ' + format(inc, '04d') if inc else 'the original'})")
    idea = backlog[0].split()[0] if backlog else "I<n>"
    lines.append(f"then      edit from {'the incumbent' if parent is None else format(parent, '04d')}, then:  "
                 f"hca try --explore --idea {idea} -m \"...\"")
    return lines


def _hint_lines(archive, ctx, floor) -> list:
    nodes, best = archive["nodes"], explore.hint(archive, ctx["n_s"])
    if best is None:
        lead = max((nodes[n]["f_raw"] - archive["f_inc"] for n in archive["elites"]), default=None)
        return ["promote   none" + ("" if lead is None else f": the best open lead is {100 * lead:+.1f} pp, short of the "
                                     f"{100 * floor:.1f} pp floor even before the selection allowance")]
    head = (f"promote   {best['n']:04d} lead {100 * best['lead']:+.1f} pp, {100 * (best['lead'] - best['allowance']):+.1f} pp after the "
            f"selection allowance over {ctx['valid_screens']} screens (floor {100 * floor:.1f} pp)")
    chain = f" · stacked chain {' → '.join(format(i, '04d') for i in best['stacked'])}: promote the tip, it carries them all" if best["stacked"] else ""
    if best["action"] == "rescreen":
        return [head + ": ambiguous, and a re-screen is cheaper than a promotion  →  "
                f'hca next --parent {best["n"]:04d} --why "re-screen", then hca try --explore -m "re-screen" with no edits' + chain]
    return [head + f'  →  hca try --promote {best["n"]:04d} -m "..."' + chain]


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
    lines.append(f"archive   {len(a['elites']) + 1} members, {len(a['cells'])} of {explore.MAX_CELLS + 1} {'files' if a['per_file'] else 'modules'} · "
                 f"{len(a['stepping_stones'])} stepping stones (screened worse than their parent) · {len(a['behind'])} behind the incumbent · "
                 f"{len(a['retired'])} retired (in the incumbent) · {len(a['tainted'])} tainted")
    return lines


def _warnings(ctx) -> list:
    entries = sorted(ctx["entries"].items())
    last_promote = max((n for n, e in entries if ledger.kind_of(e) == "promote"), default=0)
    screen = lambda e: ledger.kind_of(e) == "screen" and e.get("valid")          # noqa: E731  invalid attempts are not screens of an idea
    since_promote = sum(1 for n, e in entries if screen(e) and n > last_promote)
    confirms = ledger.read_confirms(ctx["task"])
    last_confirm = confirms[-1].get("at", "") if confirms else ""
    since_confirm = sum(1 for _, e in entries if screen(e) and e.get("at", "") > last_confirm)
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
        if idea["where"] and idea["where"] not in cells and "/".join(idea["where"].split("/")[:2]) not in cells:
            return 2
        return 3
    todo = [i for i in ctx["ideas"] if latest.get(i["id"], {}).get("class") not in ("KEPT", "NULL", "HARMFUL")]
    return [f"{i['id']} [{i['kind']}]" for i in sorted(todo, key=rank)]


# --- hca report --dream ------------------------------------------------------------------------------

DREAM_INSTRUCTION = ("Write 5-10 ideas into IDEAS.md (`## I<n> [<kind>] <title>`, a `parent: NNNN` line, `where:` and `why:`). "
                     "Each names a parent member. At least one targets an empty cell, but only if that file is plausibly hot (a profile, a "
                     "`hot:` line in IDEAS.md, or your reading says it costs time). At least one is a "
                     "stepping stone you expect to read worse first. Say which evidence above each one answers.")


def _empty_cells(root, spec, state, archive) -> tuple:
    """(cells with no member that plausibly carry weight, how they were chosen): the `hot:` files from IDEAS.md if any, else
    editable files with code weight (not metadata, small re-exports, test helpers or tiny files), as files or modules."""
    sizes = {p: n for p, n in gitops.file_sizes(root, state["last_keep"]).items() if gitops.is_editable(p, spec)}
    hot = explore.read_hot(root)
    if hot:
        files, why = [p for p in sizes if p in hot or any(p.startswith(h.rstrip("/") + "/") for h in hot)], "the hot: list in IDEAS.md"
    else:
        files, why = [p for p, n in sizes.items() if explore.weighty(p, n)], "files with code weight"
    keys = sorted({p if archive["per_file"] else "/".join(p.split("/")[:2]) for p in files})
    # A ledger from before per-file cells recorded only modules: a file under a filled module cell is not empty.
    return [k for k in keys if k not in archive["cells"] and "/".join(k.split("/")[:2]) not in archive["cells"]], why


SECTION_ITEMS = 25          # each list in DREAM.md is capped, so the packet stays bounded and the instruction always survives


def _capped(items, limit=SECTION_ITEMS) -> list:
    return items[:limit] + ([f"- (+{len(items) - limit} more)"] if len(items) > limit else [])


def dream(root, spec, state) -> Path:
    """Write `.hca/DREAM.md` from the store, the attempt records and the archive. No benchmark runs.

    Six sections; every list is capped at 25 entries plus a "(+N more)" line, so the file stays under 150 lines however
    long the climb, and the fixed instruction at the end is always there."""
    ctx = _context(root, spec, state)
    archive, records, nodes, direction = ctx["archive"], ctx["records"], ctx["archive"]["nodes"], ctx["direction"]
    cells = [f"- [{cell}] elite {info['elite']:04d}, ranked lead {100 * (info['f_rank'] - archive['f_inc']):+.1f} pp, "
             f"{info['children']} child(ren)" for cell, info in archive["cells"].items()]
    empty, why = _empty_cells(root, spec, state, archive)
    archive_map = _capped(cells) + [
        f"- empty cells ({why}): {', '.join(empty[:12]) or 'none'}" + (f" (+{len(empty) - 12} more)" if len(empty) > 12 else ""),
        f"- tainted members: {', '.join(format(n, '04d') for n in archive['tainted'][:20]) or 'none'}"]
    lineages = []
    for n in archive["elites"]:
        steps = [f"{i:04d} {_pct(records[i]['estimate_log'], direction)}" for i in nodes[n]["lineage"]
                 if records.get(i, {}).get("estimate_log") is not None]
        steps = [f"{t} (stone)" if int(t[:4]) in archive["stepping_stones"] else t for t in steps]
        lineages.append(f"- {' → '.join(steps)}" + (" (behind the incumbent)" if n in archive["behind"] else ""))
    evidence, nulls = [], {}
    for n, r in sorted(records.items()):
        if r["class"] in ("NO EVIDENCE", "SMALL") and r.get("kind") != "screen":
            ends = sorted(e for e in (r.get("lower"), r.get("upper")) if e is not None)
            interval = f" [{ledger.fmt_effect(ends[0], 'blocks')}, {ledger.fmt_effect(ends[-1], 'blocks')}]" if len(ends) == 2 else ""
            evidence.append(f"- {n:04d} {r.get('idea') or ''} {r['class']} {ledger.fmt_effect(r.get('estimate'), 'blocks')}{interval}: "
                            f"{(r.get('message') or '').splitlines()[0][:70] if r.get('message') else ''}")
        elif r["class"] == "NULL":
            nulls.setdefault(r.get("idea_kind") or "untagged", []).append(f"{n:04d}")
    evidence = _capped(evidence) + [f"- NULL ({kind}): {', '.join(ids[:20])}" for kind, ids in sorted(nulls.items())][:10]
    touched = {}
    for n in records:
        for path in _hunks(_patch(root, n)):
            touched[path] = touched.get(path, 0) + 1
    overlap = _capped([f"- {p}: {c}" for p, c in sorted(touched.items(), key=lambda kv: (-kv[1], kv[0]))], 15)
    used = ledger.confirms_used(state, ctx["task"]) + ledger.chain_of(ctx["task"])["confirms"]
    lines = [f"# Dream packet: {ctx['task']}", "", "Offline: nothing below was measured for this file. Screens are rankings, not "
             "evidence; kept estimates are selected (biased upward); only a confirm is a result.",
             "", "## 1. Archive map", "", *archive_map,
             "", "## 2. Lineages and stepping stones", "",
             f"- stepping stones (screens that read worse than their own parent): {len(archive['stepping_stones'])}; kept promotions "
             f"built on one: {len(explore.stone_keeps(ctx['task']))}", *(_capped(lineages) or ["- none yet"]),
             "", "## 3. Open evidence", "",
             *(evidence or ["- none: no reverted test left the useful effect open, and nothing tested came back NULL"]),
             "", "## 4. Overlap map (files tried, how often)", "", *(overlap or ["- none yet"]),
             "", "## 5. Resolution and budget", "",
             f"- a screen is {ctx['n_s']} blocks and resolves about {100 * ctx['resolution']:.1f}%; the keep floor is "
             f"{50 * spec.objective.useful:.1f}%", f"- hca wall so far {ctx['wall'] / 60:.1f} min; confirms left {max(0, spec.limits.confirms - used)}",
             "", "## 6. What to do with this", "", DREAM_INSTRUCTION]
    path = ledger.hca_dir(root) / "DREAM.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
