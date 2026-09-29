"""Bounded policy-code evolution over recorded, prefix-only replays.

Developer and policy programs are trusted local Python.  They are isolated into
short-lived subprocesses for a small JSON contract and timeout, not security.
"""

from __future__ import annotations

import json
import math
import copy
import subprocess
import sys
import time
import os
import signal
from pathlib import Path

from . import core
from . import stats
from .replay import replay


def _compatible(worlds):
    first = worlds[0]
    keys = ("metric", "verifier_hashes", "generator_hashes")
    if any(any(world[key] != first[key] for key in keys) or
           world["config"].get("evaluate") != first["config"].get("evaluate") or
           world["config"].get("generate") != first["config"].get("generate")
           for world in worlds[1:]):
        raise core.HarnessError("evolution pool mixes evaluator, generator, or metric contracts")


def baseline_source(policy):
    """Create a tiny serial policy program matching the configured fixed rule."""
    name = policy.get("name", "round_robin")
    if name == "executable":
        path = Path(policy["source_file"])
        return path.read_text(encoding="utf-8")
    if name not in {"round_robin", "explore", "greedy", "adaptive"}:
        raise core.HarnessError("cannot turn unknown policy into an executable baseline")
    params = {key: policy[key] for key in ("name", "width", "patience", "max_attempts") if key in policy}
    # This is deliberately compact and self contained.  The trusted driver still
    # checks the action against the observed prefix and enforces attempt caps.
    return """# Dream Loop executable baseline. Reads JSON stdin; writes {action} JSON.
import json, sys
P = %r
r = json.load(sys.stdin)
nodes = r['nodes']
if len(nodes) >= P['max_attempts']:
    answer = None
else:
    roots = [n for n in nodes if n['parent_id'] == 'root']
    children = {n['parent_id'] for n in nodes}
    leaves = [n for n in nodes if n['id'] not in children]
    if P['name'] == 'explore' or len(roots) < P['width']:
        answer = 'root'
    elif not leaves:
        answer = 'root'
    else:
        root_ids = [n['id'] for n in roots]
        by_id = {n['id']: n for n in nodes}
        def branch(node_id):
            while by_id[node_id]['parent_id'] != 'root':
                node_id = by_id[node_id]['parent_id']
            return node_id
        leaf_by_branch = {branch(n['id']): n['id'] for n in leaves}
        available = [i for i in root_ids if i in leaf_by_branch]
        if P['name'] in ('greedy', 'adaptive'):
            direction = r['metric']['direction']
            best_branch, best_score = available[0], None
            for b in available:
                for node in nodes:
                    if branch(node['id']) == b and node['status'] == 'valid' and isinstance(node['score'], (int, float)):
                        if best_score is None or (node['score'] > best_score if direction == 'max' else node['score'] < best_score):
                            best_branch, best_score = b, node['score']
            if P['name'] == 'adaptive':
                incumbent, stale = r['root']['score'], 0
                for node in nodes:
                    if node['status'] == 'valid' and isinstance(node['score'], (int, float)):
                        improved = node['score'] > incumbent if direction == 'max' else node['score'] < incumbent
                        incumbent, stale = (node['score'], 0) if improved else (incumbent, stale + 1)
                answer = None if stale >= P['patience'] else leaf_by_branch[best_branch]
            else:
                answer = leaf_by_branch[best_branch]
        else:
            last = r.get('trace', [])[-1].get('action') if r.get('trace') else nodes[-1]['id']
            last_branch = branch(last) if last in by_id else None
            index = (available.index(last_branch) + 1) %% len(available) if last_branch in available else 0
            answer = leaf_by_branch[available[index]]
print(json.dumps({'action': answer}))
""" % params


def _write_source(path, source):
    if not isinstance(source, str) or not source.strip() or len(source.encode()) > 200_000:
        raise core.HarnessError("policy source must be nonempty and at most 200 KB")
    Path(path).write_text(source, encoding="utf-8")
    return core.digest(path)


def _policy(path, source_hash, cap, timeout):
    return {"name": "executable", "source_file": str(Path(path).resolve()),
            "source_hash": source_hash, "timeout_seconds": timeout,
            "max_attempts": cap, "width": 1, "patience": 1}


def score_policy(worlds, policy, penalty, deadline=None):
    episodes = []
    for world in worlds:
        if deadline is not None and time.monotonic() >= deadline:
            raise core.HarnessError("evolution wall deadline exhausted during replay")
        episodes.append(replay(world, policy, deadline=deadline))
    eligible = all(item["stop_reason"] == "policy_stop" for item in episodes)
    utility = (sum(item["improvement"] - penalty * item["attempts"] for item in episodes) / len(episodes)
               if eligible else None)
    return {"policy": policy, "eligible": eligible, "utility": utility, "episodes": episodes}


def world_utilities(worlds, scored, penalty):
    """Per-world replay utilities as paired items; gains are already direction-adjusted."""
    return [{"id": world["world_id"], "score": episode["improvement"] - penalty * episode["attempts"]}
            for world, episode in zip(worlds, scored["episodes"])]


def paired_verdict(worlds, candidate, incumbent, penalty, rule):
    """Noise-aware comparison of two supported policies on the same recorded worlds."""
    if not (candidate["eligible"] and incumbent["eligible"]):
        return None
    return stats.decide(world_utilities(worlds, candidate, penalty),
                        world_utilities(worlds, incumbent, penalty), "max", rule)


def equivalent_score(left, right):
    """Compare replay evidence while allowing portable materialized source paths."""
    def normalize(value):
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items() if key != "source_file"}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value
    return normalize(left) == normalize(right)


def _developer(argv, request, timeout):
    process = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=(os.name == "posix"))
        stdout, stderr = process.communicate(json.dumps(request, sort_keys=True), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.communicate()
        raise core.HarnessError("policy developer timed out") from exc
    if process.returncode:
        raise core.HarnessError("policy developer failed")
    if len(stdout.encode()) > 300_000:
        raise core.HarnessError("policy developer stdout exceeds 300 KB")
    try:
        reply = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise core.HarnessError("policy developer must emit one JSON object") from exc
    if not isinstance(reply, dict) or not isinstance(reply.get("source"), str):
        raise core.HarnessError("policy developer reply needs string source")
    if set(reply) - {"source", "summary"} or ("summary" in reply and not isinstance(reply["summary"], str)):
        raise core.HarnessError("policy developer reply permits only source and summary")
    return reply, time.monotonic() - started


def _feedback(scored):
    """Replay trajectories/scores only; no filesystem references or hidden world."""
    return [{key: episode.get(key) for key in
             ("trace", "observed_ids", "stop_reason", "unsupported_reason", "attempts", "cost_seconds", "best_score", "improvement")}
            for episode in scored["episodes"]]


def evolve(runs, developer_argv, revisions=4, timeout_seconds=10, policy_seconds=2, wall_seconds=60, penalty=0.0,
           output=None, initial_source=None, promotion="point", rule=None, confirm_runs=None):
    """Iteratively ask a developer for policy code and retain the replay incumbent.

    This measures fit to recorded worlds only.  The selected source must still
    be tested on a fresh rollout before any performance claim.

    promotion="point" accepts any higher mean replay utility (the original rule).
    promotion="paired" accepts a revision only when the per-world paired interval,
    Bonferroni-corrected over all requested revisions, excludes zero and the
    guardrails hold. Every revision records the paired diagnostic either way.
    The in-sample paired check measures noise across worlds; it does not correct
    for a developer that has adapted to those same worlds' feedback.
    confirm_runs are held-out recorded worlds the developer never sees (20% of the
    wall budget is reserved for them): the final selection is re-checked against
    the initial policy there. Under "paired" promotion a failed confirmation
    reverts to the initial policy when that policy is supported; otherwise the
    selection is kept and marked unconfirmed.
    """
    if promotion not in ("point", "paired"):
        raise core.HarnessError("promotion must be point or paired")
    rule = stats.check_rule(rule)
    rule["comparisons"] = max(rule["comparisons"], revisions if isinstance(revisions, int) else 1)
    if not runs or not isinstance(revisions, int) or revisions < 1:
        raise core.HarnessError("evolution needs at least one run and one revision")
    if not isinstance(developer_argv, list) or not developer_argv or not all(isinstance(x, str) and x for x in developer_argv):
        raise core.HarnessError("developer command must be a nonempty argv array")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (timeout_seconds, policy_seconds, wall_seconds)):
        raise core.HarnessError("developer and evolution deadlines must be finite and positive")
    if not math.isfinite(penalty) or penalty < 0:
        raise core.HarnessError("attempt penalty must be finite and nonnegative")
    worlds = [core.verify(run) for run in runs]
    confirm_runs = list(confirm_runs or [])
    confirm_worlds = [core.verify(run) for run in confirm_runs]
    _compatible(worlds + confirm_worlds)
    if len({world["world_id"] for world in worlds + confirm_worlds}) != len(worlds) + len(confirm_worlds):
        raise core.HarnessError("duplicate world in evolution pool or shared between select and confirm sets")
    cap = min(world["config"]["limits"]["attempts"] for world in worlds)
    output = Path(output).resolve() if output else None
    if output is None or output.exists():
        raise core.HarnessError("evolution output must be a new path")
    source_dir = output.with_name(output.stem + ".policies")
    source_dir.mkdir(parents=True)
    started = time.monotonic()
    deadline = started + wall_seconds
    source = Path(initial_source).read_text(encoding="utf-8") if initial_source else baseline_source(worlds[-1]["config"]["policy"])
    initial_path = source_dir / "p000.py"
    initial_hash = _write_source(initial_path, source)
    initial_replay_started = time.monotonic()
    initial_evaluation = score_policy(worlds, _policy(initial_path, initial_hash, cap, policy_seconds), penalty, deadline)
    initial_replay_seconds = time.monotonic() - initial_replay_started
    incumbent = initial_evaluation
    development_history = []
    revision_records = []
    # Held-out confirmation gets a reserved share of the wall budget.
    reserve = 0.2 * wall_seconds if confirm_worlds else 0.0
    for revision in range(1, revisions + 1):
        remaining = wall_seconds - reserve - (time.monotonic() - started)
        if remaining <= 0:
            break
        request = {
            "schema_version": 1, "kind": "dream_loop_policy_development_request",
            "revision": revision, "metric": worlds[0]["metric"],
            "objective": "mean(direction-adjusted gain - attempt_penalty * revealed attempts); only wholly supported replays are eligible",
            "attempt_penalty": penalty,
            "incumbent": {"source": Path(incumbent["policy"]["source_file"]).read_text(encoding="utf-8"),
                          "sha256": incumbent["policy"]["source_hash"], "utility": incumbent["utility"],
                          "eligible": incumbent["eligible"], "feedback": _feedback(incumbent)},
            "history": copy.deepcopy(development_history),
            "constraints": ["Write complete Python policy source.", "It receives only JSON policy requests on stdin and emits exactly {action} JSON.",
                            "Use only the supplied observed prefix; the driver rejects unavailable actions.",
                            "Do not modify evaluator or candidate code."],
        }
        revision_started = time.monotonic()
        record = {"revision": revision, "request": request, "timing": {}}
        try:
            reply, developer_elapsed = _developer(developer_argv, request, min(timeout_seconds, remaining))
            record["timing"]["developer_seconds"] = developer_elapsed
            record["reply"] = reply
            candidate_path = source_dir / ("p%03d.py" % revision)
            candidate_hash = _write_source(candidate_path, reply["source"])
            replay_started = time.monotonic()
            candidate = score_policy(worlds, _policy(candidate_path, candidate_hash, cap, policy_seconds), penalty, deadline)
            record["timing"]["replay_seconds"] = time.monotonic() - replay_started
            paired = paired_verdict(worlds, candidate, incumbent, penalty, rule)
            if promotion == "paired" and paired is not None:
                accepted = paired["verdict"] == "promote"
            else:
                accepted = candidate["eligible"] and (not incumbent["eligible"] or candidate["utility"] > incumbent["utility"])
            record.update({"developer_summary": reply.get("summary", ""), "candidate_source": reply["source"],
                           "candidate_hash": candidate_hash, "candidate": candidate, "accepted": accepted,
                           "paired": paired})
            if accepted:
                incumbent = candidate
        except (ValueError, OSError, core.HarnessError) as exc:
            record.update({"accepted": False, "error": str(exc)})
        record["timing"]["elapsed_seconds"] = time.monotonic() - revision_started
        # Later developer calls see prior code + replay feedback, never source-world paths.
        development_history.append({key: record[key] for key in
                                    ("revision", "candidate_source", "candidate_hash", "developer_summary", "accepted", "error") if key in record} |
                                   ({"feedback": _feedback(record["candidate"])} if "candidate" in record else {}) |
                                   ({"paired_verdict": record["paired"]["verdict"], "paired_reasons": record["paired"]["reasons"]}
                                    if record.get("paired") else {}))
        revision_records.append(record)
    confirmation = None
    if confirm_worlds:
        confirmation = {"worlds": [{"id": w["world_id"], "file": str(Path(run).resolve() / "world.json"),
                                    "sha256": core.digest(Path(run) / "world.json")} for run, w in zip(confirm_runs, confirm_worlds)],
                        "reverted_to_initial": False}
        if incumbent["policy"]["source_hash"] == initial_hash:
            confirmation.update(verdict="not_needed", reasons=["the initial policy was retained"], unconfirmed=False)
        else:
            try:
                selected_confirm = score_policy(confirm_worlds, incumbent["policy"], penalty, deadline)
                initial_confirm = score_policy(confirm_worlds, initial_evaluation["policy"], penalty, deadline)
                check = paired_verdict(confirm_worlds, selected_confirm, initial_confirm, penalty, dict(rule, comparisons=1))
                confirmation.update(selected_utility=selected_confirm["utility"], initial_utility=initial_confirm["utility"])
                if check is None:
                    confirmation.update(verdict="unsupported", reasons=["a policy was not fully supported on the confirm worlds"])
                else:
                    confirmation.update(check, verdict=check["verdict"])
            except (ValueError, OSError) as exc:
                confirmation.update(verdict="error", reasons=[str(exc)])
            confirmation["unconfirmed"] = confirmation["verdict"] != "promote"
            # Keeping the incumbent means keeping a usable one: never revert to an unsupported start.
            if promotion == "paired" and confirmation["unconfirmed"] and initial_evaluation["eligible"]:
                confirmation["reverted_to_initial"] = True
                incumbent = initial_evaluation
    result = {
        "schema_version": 1, "kind": "policy_code_evolution_v1", "metric": worlds[0]["metric"],
        "verifier_hashes": worlds[0]["verifier_hashes"], "attempt_penalty": penalty,
        "project_contract": {"evaluate": worlds[0]["config"].get("evaluate"),
                             "generate": worlds[0]["config"].get("generate")},
        "generator_hashes": worlds[0]["generator_hashes"],
        "objective": "mean(direction-adjusted gain - attempt_penalty * revealed attempts)",
        "source_worlds": [{"id": w["world_id"], "file": str(Path(run).resolve() / "world.json"),
                           "sha256": core.digest(Path(run) / "world.json")} for run, w in zip(runs, worlds)],
        "developer": {"argv": developer_argv, "revisions_requested": revisions, "per_revision_seconds": timeout_seconds,
                      "policy_seconds": policy_seconds, "wall_seconds": wall_seconds},
        "timing": {"initial_replay_seconds": initial_replay_seconds,
                   "elapsed_seconds": time.monotonic() - started},
        "initial_evaluation": initial_evaluation,
        "selected_evaluation": incumbent, "selected_source": Path(incumbent["policy"]["source_file"]).read_text(encoding="utf-8"),
        "selected_source_hash": incumbent["policy"]["source_hash"], "revisions": revision_records,
        "promotion": promotion, "decision_rule": rule, "confirmation": confirmation,
        "claim": "In-sample replay selection over recorded trajectories. The developer and policy are trusted local code; subprocesses provide I/O and timeout boundaries, not a security sandbox. No prospective improvement is claimed.",
    }
    core.write_json(output, result)
    return result


def materialize_selected_source(selection_path, selection):
    """Make an evolution selection portable enough for `init --policy-from`."""
    if selection.get("kind") != "policy_code_evolution_v1":
        return None
    source = selection.get("selected_source")
    expected = selection.get("selected_source_hash")
    if not isinstance(source, str) or not isinstance(expected, str):
        raise core.HarnessError("evolution selection lacks selected source")
    target = Path(selection_path).resolve().with_name(Path(selection_path).stem + ".selected.py")
    if target.exists() and core.digest(target) != expected:
        raise core.HarnessError("materialized selected policy changed")
    if not target.exists():
        _write_source(target, source)
    if core.digest(target) != expected:
        raise core.HarnessError("selected source hash does not match selection")
    return _policy(target, expected, selection["selected_evaluation"]["policy"]["max_attempts"],
                   selection["selected_evaluation"]["policy"]["timeout_seconds"])
