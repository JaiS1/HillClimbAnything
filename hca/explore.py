"""Explore mode: ideas, outcome classes, the archive of attempt snapshots, the parent sampler and the promotion checks.

The design is docs/DESIGN-climb-engine.md (v0.5, §3). Every screened child is an attempt snapshot pinned at
`refs/hca/attempts/NNNN`, so an archive member is a program HCA can restore or measure again. A screen measures a child
against its parent in a few blocks, only to rank it; it never keeps (the screen path in try_ cannot reach the keep).
Only a promotion, a normal referee try of a member against the incumbent on fresh blocks, can keep.

Everything here that matters for honesty is derived from the referee store and the refs, never from `.hca/archive.json`
or the agent-writable verdict.json: the archive is a cache for reading, and promotion re-derives validity, lineage,
taint and replay status itself (N3). Standard library only.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import statistics
from pathlib import Path

from . import gitops, ledger, stats
from .core import Refusal, object_hash, write_json

IDEAS_FILE = "IDEAS.md"
IDEA_KINDS = ("avoid-work", "algorithm", "data-structure", "allocation", "dispatch", "io", "concurrency", "config", "micro", "other")
_IDEA = re.compile(r"^##\s+(I\d+\w*)\s+\[([a-z-]+)\]\s*(.*?)\s*$")
SAMPLER = "w1"
INCUMBENT_MASS = 0.40
MAX_CELLS = 6                    # files outside the six most-touched modules share the cell `other`
MEMBER_ARMS = 6                  # arms kept for archive members besides init, confirmed and last keep (S5)
PER_FILE_CELLS = 25              # an editable set this small gets one cell per file, else one per two-component module
# Review pointers: not a judge, a list of places a human should look. Fixed here rather than in hca.toml, so the agent
# whose patches it scans cannot switch it off.
POINTERS = re.compile(r"cache|memo|lru_cache|pickle|open\(|os\.environ|sys\.argv|thread|subprocess|ctypes|Proxy|setTimeout|process\.|eval")


# --- ideas and outcome classes ---------------------------------------------------------------------

def read_ideas(root) -> list:
    """IDEAS.md as [{id, kind, title, parent, where}]. Bad grammar never fails anything: unparsed lines are skipped."""
    try:
        text = (Path(root) / IDEAS_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    ideas = []
    for line in text.splitlines():
        head = _IDEA.match(line)
        if head:
            kind = head.group(2) if head.group(2) in IDEA_KINDS else "untagged"
            ideas.append({"id": head.group(1), "kind": kind, "title": head.group(3), "parent": None, "where": None})
        elif ideas and ":" in line:
            key, _, value = line.partition(":")
            if key.strip() == "parent" and value.strip().isdigit():
                ideas[-1]["parent"] = int(value.strip())
            elif key.strip() == "where":
                ideas[-1]["where"] = value.strip()
    return ideas


def backlog(root) -> dict | None:
    """The backlog snapshot a try records: idea ids in file order and the file's sha256."""
    path = Path(root) / IDEAS_FILE
    if not path.is_file():
        return None
    return {"ids": [i["id"] for i in read_ideas(root)], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def idea_kind(root, idea) -> str:
    return next((i["kind"] for i in read_ideas(root) if i["id"] == idea), "untagged")


def outcome_class(rec, useful, direction) -> str:
    """KEPT | SMALL | NO EVIDENCE | NULL | HARMFUL | SCREENED | INVALID, from one attempt record (design §3a)."""
    verdict = rec.get("verdict")
    if rec.get("kind") == "screen":
        return "SCREENED" if verdict == "screened" else "INVALID"
    if verdict == "keep":
        return "KEPT"
    if verdict != "revert" or not rec.get("valid") or rec.get("estimate_log") is None:
        return "INVALID"
    if rec["estimate_log"] < 0 and rec.get("p") is not None and rec.get("upper") is not None \
            and rec["upper"] != 0 and not ledger.improves(rec["upper"], "blocks", direction):
        return "HARMFUL"
    if rec.get("p") is not None and rec["p"] <= rec.get("level", 0) and rec["estimate_log"] < 0.5 * useful:
        return "SMALL"
    return "NO EVIDENCE" if rec.get("includes_useful") else "NULL"


def pointers(patch) -> list:
    """The review-pointer words found on the added lines of a patch."""
    found = set()
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            found.update(m.group(0) for m in POINTERS.finditer(line))
    return sorted(found)


def screen_blocks(state, spec) -> int:
    """n_s = clamp(ceil((3 sigma / F)^2), 5, 8): never below 5, where a 95% bound first exists (S7); 8 without an A/A."""
    sigma, floor = (state.get("noise") or {}).get("noise_sd"), 0.5 * spec.objective.useful
    if not sigma or floor <= 0:
        return 8
    return min(8, max(5, math.ceil((3 * sigma / floor) ** 2)))


def module_of(changes) -> str:
    """The module key of a change: the editable file with the most changed lines, as its first two path components."""
    if not changes:
        return "other"
    return "/".join(file_of(changes).split("/")[:2])


def file_of(changes) -> str:
    """The editable file with the most changed lines (the cell key when the editable set is small)."""
    return max(sorted(changes), key=lambda p: changes[p]) if changes else "other"


SMALL_BYTES = 400                # below this a file carries no code weight worth a cell
SMALL_INIT_BYTES = 2000          # an __init__.py or __main__.py below this is a re-export, not a hot path


def read_hot(root) -> list:
    """Hotspot files the agent declared in IDEAS.md (`hot: a.py, b.py` lines, from a profile): the only empty-cell candidates."""
    try:
        text = (Path(root) / IDEAS_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    hot = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*+").strip()                 # `hot:`, `- hot:` and `* hot:` all count
        if line.lower().startswith("hot:"):
            for item in line[4:].split(","):
                item = item.strip().strip("`").replace("\\", "/")
                while item.startswith("./"):
                    item = item[2:]
                if item:
                    hot.append(item)
    return hot


def weighty(path, size) -> bool:
    """Does an editable file plausibly carry code weight? Metadata, small re-export modules, test helpers and tiny files do not."""
    name = path.rsplit("/", 1)[-1]
    parts = path.lower().split("/")
    if name in ("__meta__.py", "__about__.py", "__version__.py", "_version.py", "version.py"):
        return False
    if name in ("__init__.py", "__main__.py") and size < SMALL_INIT_BYTES:
        return False
    if any(part in ("test", "tests", "spec", "__tests__") or part.startswith("test_") for part in parts[:-1]) \
            or name.startswith("test_") or name.startswith("test.") or "_test." in name or ".test." in name or ".spec." in name:
        return False
    return size >= SMALL_BYTES


# --- the archive, derived from the referee store --------------------------------------------------------

def entries(task) -> dict:
    return {e["n"]: e for e in ledger.session_attempts(task) if e.get("n") is not None}


def keep_fitness(root, state, by_n) -> tuple:
    """({commit sha: f}, incumbent id) over the original, every keep and every restore on the branch: f is the sum of the
    referee estimates of the keeps a commit carries; a restore returns to the last keep a failed confirm did not undo."""
    f, current, confirmed, inc, inc_confirmed = {state["init_sha"]: 0.0}, 0.0, 0.0, 0, 0
    for sha, n in gitops.keep_commits(root, state["init_sha"], state["last_keep"]):
        if n is None:
            current, inc = confirmed, inc_confirmed
        else:
            current, inc = current + (by_n.get(n, {}).get("estimate_log") or 0.0), n
            if not by_n.get(n, {}).get("unconfirmed"):
                confirmed, inc_confirmed = current, n
        f[sha] = current
    return f, inc


def _chain(by_n, n) -> list:
    chain = []
    while n is not None and n in by_n and n not in chain:
        chain.insert(0, n)
        n = by_n[n].get("parent")
    return chain


def keep_contained(root, by_n, keep_sha, n, sha, mine=None) -> bool:
    """Does the tree at `sha` contain keep `n` (commit `keep_sha`)? Not if a path the keep changed is back at the keep's
    parent's content (undone by hand). Otherwise yes if `sha` descends from the keep, or from the member a promotion keep
    promoted (a promotion's keep is a fresh commit on the last keep, so the member's children never descend from it),
    or if every path the keep changed has the keep's content."""
    before, after = gitops.tree_entries(root, f"{keep_sha}^"), gitops.tree_entries(root, keep_sha)
    changed = [p for p in set(before) | set(after) if before.get(p) != after.get(p)]
    mine = gitops.tree_entries(root, sha) if mine is None else mine
    if any(mine.get(p) == before.get(p) for p in changed):
        return False
    promoted = by_n.get(by_n.get(n, {}).get("member"), {}).get("sha")
    if gitops.is_ancestor(root, keep_sha, sha) or (promoted and gitops.is_ancestor(root, promoted, sha)):
        return True
    return all(mine.get(p) == after.get(p) for p in changed)


def retired_members(root, state, by_n) -> set:
    """Members absorbed by a promotion still in the incumbent: the promoted member and its ancestors on the parent chain.
    They stay in the archive's history but are never sampled, inspired from or hinted again (their changes are already in)."""
    out, mine = set(), gitops.tree_entries(root, state["last_keep"])
    for k, n in gitops.keep_commits(root, state["init_sha"], state["last_keep"]):
        e = by_n.get(n, {}) if n else {}
        if e.get("member") is not None and not e.get("unconfirmed") and keep_contained(root, by_n, k, n, state["last_keep"], mine):
            out.update(_chain(by_n, e["member"]))
    return out


def build(root, spec, state, seed=None) -> dict:
    """The archive: members with raw and ranking fitness, one elite per cell, stepping stones, retired and tainted ids.

    `f_raw` telescopes along a lineage (parent's f_raw + this screen's Hodges-Lehmann log estimate vs that parent; a
    referee try carries its own estimate); `f_rank = f_raw - SE_s * d` shrinks it by the screen's standard error per
    screened step not yet in the incumbent (S9). A re-screen (same tree, same parent) supersedes the screen whose blocks it
    pooled. Cells are files when the editable set has at most 25 files, else two-component modules. A stepping stone is a
    screen that read worse than its own parent; "behind" is an elite whose ranked fitness trails the incumbent's.
    """
    by_n = entries(state["task"])
    f_keep, incumbent = keep_fitness(root, state, by_n)
    per_file = len([p for p in gitops.file_sizes(root, state["last_keep"]) if gitops.is_editable(p, spec)]) <= PER_FILE_CELLS
    nodes, latest = {}, {}
    f_inc, retired = f_keep.get(state["last_keep"], 0.0), retired_members(root, state, by_n)
    for n in sorted(by_n):
        e = by_n[n]
        if not e.get("sha") or not e.get("valid") or e.get("estimate_log") is None:
            continue
        up, screened = nodes.get(e.get("parent")), ledger.kind_of(e) == "screen"
        # A retired parent is already in the incumbent: its child starts from the incumbent's own fitness, so the lead is the
        # sum of the open steps only (never the screens' chained estimate of what a promotion has since re-measured).
        start = f_inc if up and up["n"] in retired else up["f_raw"] if up else f_keep.get(e.get("base"), 0.0)
        nodes[n] = {"n": n, "sha": e["sha"], "parent": e.get("parent"), "screened": screened, "step": e["estimate_log"],
                    "module": (e.get("file") if per_file else None) or e.get("module") or "other", "se": e.get("se") or 0.0,
                    "f_raw": f_keep.get(e["sha"], start + e["estimate_log"]) if e.get("verdict") == "keep" else start + e["estimate_log"],
                    "tainted": bool(e.get("tainted")), "blocks": e.get("blocks", 0), "lineage": (up["lineage"] if up else []) + [n]}
        if screened:
            latest[(e.get("tree"), e.get("base"))] = n
    for node in nodes.values():
        open_steps = [i for i in node["lineage"] if nodes[i]["screened"] and i not in retired]
        node.update(retired=node["n"] in retired, d=len(open_steps), var=sum(nodes[i]["se"] ** 2 for i in open_steps))
        node["f_rank"] = node["f_raw"] - node["se"] * node["d"]
    screens = [nodes[n] for n in sorted(set(latest.values()))]
    touched = {}
    for node in screens:
        touched[node["module"]] = touched.get(node["module"], 0) + 1
    named = sorted(touched, key=lambda m: (-touched[m], m))[:MAX_CELLS]
    cells = {}
    for node in screens:                                  # admission in attempt order: an empty cell, or a better ranked fitness
        node["cell"] = node["module"] if node["module"] in named else "other"
        elite = cells.get(node["cell"])
        if not node["tainted"] and not node["retired"] and (elite is None or node["f_rank"] > nodes[elite]["f_rank"]):
            cells[node["cell"]] = node["n"]
    children = {}
    for e in by_n.values():
        if ledger.kind_of(e) == "screen" and e.get("parent") is not None:
            children[e["parent"]] = children.get(e["parent"], 0) + 1
    elites = sorted(cells.values())
    return {"seed": seed, "incumbent": incumbent, "f_inc": f_inc, "nodes": nodes, "elites": elites, "children": children,
            "cells": {c: {"elite": n, "f_rank": round(nodes[n]["f_rank"], 6), "children": children.get(n, 0)} for c, n in sorted(cells.items())},
            "stepping_stones": sorted(n for n in set(latest.values()) if nodes[n]["step"] < 0),
            "behind": [n for n in elites if nodes[n]["f_rank"] < f_inc], "retired": sorted(retired & set(nodes)),
            "tainted": sorted(n for n, node in nodes.items() if node["tainted"]), "floor": 0.5 * spec.objective.useful,
            "reverted": sorted({e.get("member") for e in by_n.values() if ledger.kind_of(e) == "promote" and e.get("verdict") == "revert"} - {None}),
            "per_file": per_file, "screens": sum(1 for e in by_n.values() if ledger.kind_of(e) == "screen" and e.get("valid"))}


def write_cache(root, archive) -> None:
    """`.hca/archive.json`: a derived view (regenerable byte-identically from the store and the refs; nothing reads it back)."""
    write_json(ledger.hca_dir(root) / "archive.json",
               {"seed": archive["seed"], "sampler": SAMPLER, "incumbent": archive["incumbent"], "cells": archive["cells"],
                "members": sorted([archive["incumbent"]] + archive["elites"]), "stepping_stones": archive["stepping_stones"],
                "behind_incumbent": archive["behind"], "retired": archive["retired"], "tainted": archive["tainted"]})


def climb_seed(state) -> int:
    """The sampler's seed: fixed per climb by the store id, and separate from every try's block-order seed (S2)."""
    return int(str(state.get("store_id") or "0")[:12] or "0", 16)


def sample(archive, screens, seed) -> dict:
    """Draw the next parent: the incumbent with mass 0.40, other elites by sigmoid((f - f_med) / s) / (1 + children) over
    the remaining 0.60, members more than 2F behind the incumbent excluded. Returns the full sampling log (N5)."""
    nodes, floor, f_inc = archive["nodes"], archive["floor"], archive["f_inc"]
    others = [n for n in archive["elites"] if f_inc - nodes[n]["f_rank"] <= 2 * floor]
    fs = [f_inc] + [nodes[n]["f_rank"] for n in archive["elites"]]
    centre, spread = statistics.median(fs), max(floor, stats.robust_sd(fs) if len(fs) > 1 else 0.0) or 1e-9
    raw = [1 / (1 + math.exp(-(nodes[n]["f_rank"] - centre) / spread)) / (1 + archive["children"].get(n, 0)) for n in others]
    candidates = [archive["incumbent"]] + others
    weights = [1.0] if not raw else [INCUMBENT_MASS] + [(1 - INCUMBENT_MASS) * w / sum(raw) for w in raw]
    rng = random.Random(f"hca-sampler:{seed}:{screens}")      # its own stream: never the block-order RNG of any try
    draw, total, chosen = rng.random(), 0.0, len(weights) - 1
    for i, w in enumerate(weights):
        total += w
        if draw < total:
            chosen = i
            break
    weights = [round(w, 6) for w in weights]
    return {"sampler": SAMPLER, "candidates": candidates, "weights": weights, "weights_hash": object_hash([candidates, weights])[:16],
            "parent": candidates[chosen], "parent_p": weights[chosen]}


Z95 = 1.645


def hint(archive, n_s) -> dict | None:
    """What to do with the best elite, or None.

    lead = f_raw - f_inc (the chained screen estimates still open, against the incumbent) and A = SE_chain * sqrt(2 ln M),
    M the number of valid screens: every one was a candidate in the selection that ranks this member first.
    - `promote` when lead - A >= F: even after the selection allowance the member clears the keep floor. A promotion is
      itself a fresh test at alpha, so a big lead never waits for a re-screen however noisy the screen was.
    - `rescreen` when it does not, but lead + 1.645 SE still reaches F (ambiguous) and the member has not been re-screened:
      a re-screen costs n_s blocks against a promotion's 10 to 20.
    `stacked` lists the open screened steps that each improved on their parent when there are two or more (a chain to promote
    as one tree). A member whose promotion already reverted is never hinted again (re-promoting it needs --again).
    """
    nodes, floor, f_inc = archive["nodes"], archive["floor"], archive["f_inc"]
    m, best = max(1, archive.get("screens", 1)), None
    for n in archive["elites"]:
        if n in archive.get("reverted", ()):
            continue
        node = nodes[n]
        se = math.sqrt(node["var"])
        allowance = se * math.sqrt(2 * math.log(m))
        lead = node["f_raw"] - f_inc
        if lead - allowance >= floor:
            action = "promote"
        elif lead + Z95 * se >= floor and node["blocks"] <= n_s:
            action = "rescreen"
        else:
            continue
        key = (action == "promote", lead - allowance)
        if best is None or key > best["key"]:
            stacked = [i for i in node["lineage"] if nodes[i]["screened"] and not nodes[i]["retired"] and nodes[i]["step"] > 0]
            best = {"n": n, "key": key, "action": action, "lead": lead, "allowance": allowance, "needed": floor + allowance,
                    "rescreen": action == "rescreen", "stacked": stacked if len(stacked) >= 2 else []}
    return best


def stone_keeps(task) -> list:
    """Kept promotions whose member's lineage holds a stepping stone (a screen that read worse than its own parent)."""
    by_n = entries(task)
    stone = lambda i: ledger.kind_of(by_n[i]) == "screen" and (by_n[i].get("estimate_log") or 0) < 0     # noqa: E731
    return [n for n, e in sorted(by_n.items()) if ledger.kind_of(e) == "promote" and e.get("verdict") == "keep"
            and any(stone(i) for i in _chain(by_n, e.get("member")))]


# --- parents, promotions and taint (all from the store and the refs) -----------------------------------

def member(root, state, n, allow_tainted=False, what="promote") -> dict:
    """Attempt `n`'s store entry, checked against its ref: this session's, valid, a snapshot whose ref and tree match the
    store, and (unless allowed) not tainted. Raises Refusal otherwise. Never reads archive.json or verdict.json (N3)."""
    n = int(n)
    entry = entries(state["task"]).get(n)
    if entry is None or not entry.get("sha"):
        raise Refusal(f"attempt {n:04d} is not a measured attempt of this session in the referee store, so it cannot be a {what}.")
    if not entry.get("valid"):
        raise Refusal(f"attempt {n:04d} was not a valid measurement (invalid, void or failed), so it cannot be a {what}.")
    ref = gitops.git(root, "rev-parse", "-q", "--verify", gitops.attempt_ref(n), check=False).strip()
    if ref != entry["sha"] or gitops.tree_of(root, ref) != entry.get("tree"):
        raise Refusal(f"refs/hca/attempts/{n:04d} does not match the referee store's snapshot for that attempt. Fix: ask the human.")
    if entry.get("tainted") and not allow_tainted:
        raise Refusal(f"attempt {n:04d} is tainted: its lineage holds a keep that a failed confirm undid. "
                      + ("Promoting it needs --tainted (recorded and shown in the report)." if what == "promote" else f"It cannot be a {what}."))
    return entry


def lineage(task, n) -> list:
    """Attempt ids from the first measured ancestor down to `n`, following the store's parent links."""
    return _chain(entries(task), n)


def dropped_keeps(root, state, sha, tip=None) -> list:
    """Keeps on the branch up to `tip` (default the last keep; not undone by a failed confirm) that the tree at `sha` does not
    contain: promoting it drops them (S1). Containment follows the member a promotion keep promoted, so a child of a promoted
    member keeps that promotion."""
    by_n, mine = entries(state["task"]), gitops.tree_entries(root, sha)
    return [n for k, n in gitops.keep_commits(root, state["init_sha"], tip or state["last_keep"])
            if n and not by_n.get(n, {}).get("unconfirmed") and not keep_contained(root, by_n, k, n, sha, mine)]


def dropped_at(root, state, n) -> list:
    """The keeps promotion `n` dropped, recomputed from the store and the refs against the last keep it was measured against
    (so a ledger written before containment followed promoted members reads correctly)."""
    by_n = entries(state["task"])
    e = by_n.get(n, {})
    member_sha = by_n.get(e.get("member"), {}).get("sha")
    return dropped_keeps(root, state, member_sha, e["base"]) if member_sha and e.get("base") else []


def taint_after_fail(root, state, failed_tip, target) -> list:
    """Confirm FAIL hook (B2): mark the undone keeps unconfirmed, taint every measured attempt built on one of them, and reset
    the explore parent to the new incumbent. Idempotent (crash recovery calls it again). Returns the newly tainted ids.

    "Built on" is content, not only git ancestry: a promotion's keep is a fresh commit on the last keep, so the member it
    promoted and that member's children do not descend from it. An attempt is tainted if its snapshot descends from an undone
    keep, if its tree is an undone keep's tree, if it is the member an undone promotion promoted, or if any ancestor on its
    store parent chain is one of those."""
    task = state["task"]
    undone = [(k, n) for k, n in gitops.keep_commits(root, target, failed_tip) if n]
    for _, n in undone:
        ledger.update_entry(task, n, unconfirmed=True)
    by_n = entries(task)
    keeps = {n for _, n in undone}
    trees = {gitops.tree_of(root, k) for k, _ in undone}
    promoted = {by_n.get(n, {}).get("member") for n in keeps} - {None}

    def bad(n):
        e = by_n.get(n, {})
        return e.get("tree") in trees or n in promoted

    tainted = []
    for n, e in sorted(by_n.items()):
        if not e.get("sha") or e.get("tainted") or n in keeps:
            continue
        chain = lineage(task, n)
        if any(bad(i) for i in chain) or any(gitops.is_ancestor(root, k, e["sha"]) for k, _ in undone):
            ledger.update_entry(task, n, tainted=True)
            tainted.append(n)
    if state.get("explore") is not None:
        state["explore"] = {"parent": None, "sampling": None}
    return tainted


def parent_base(root, state) -> tuple:
    """(parent id or None, base sha): the explore parent the working tree is at, re-derived from the store and the refs."""
    parent = (state.get("explore") or {}).get("parent")
    if parent is None:
        return None, state["last_keep"]
    return parent, member(root, state, parent, what="parent")["sha"]


def arm_shas(state) -> list:
    """Arms to keep after a try: init, last confirmed, last keep; in explore mode also the parent and the newest members."""
    keep = [state["init_sha"], state.get("last_confirmed"), state["last_keep"]]
    if state.get("explore") is None:
        return keep
    by_n, parent = entries(state["task"]), state["explore"].get("parent")
    recent = [e["sha"] for n, e in sorted(by_n.items(), reverse=True) if e.get("sha") and e.get("valid") and n != parent]
    return keep + [by_n.get(parent, {}).get("sha")] + recent[:MEMBER_ARMS - 1]
