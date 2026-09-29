"""Prefix-only replay for small, recorded discovery worlds.

``select_action`` receives only ``root``, already revealed ``nodes``, the metric,
and (when called by :func:`replay`) past actions.  It never receives a world or
asks whether an action has a recorded child.  The driver alone resolves actions:
``root`` requests the next registered root branch and a node id requests its one
recorded continuation.  A missing continuation or unmet context dependency is an
unsupported stop, never a zero-valued observation.

Adaptive is deliberately modest: it selects as greedy does, but stops before a
new action after ``patience`` consecutive *valid* revealed nodes failed to improve
the incumbent (root score is the initial incumbent).  Invalid and blocked nodes
do not reset or increment that counter and never affect quality.
"""

from __future__ import annotations

import math
import json
import subprocess
import sys
import time
import os
import signal
from pathlib import Path
from collections.abc import Mapping


_STATUSES = {"valid", "invalid", "blocked"}
_POLICIES = {"round_robin", "greedy", "explore", "adaptive", "executable"}
_DEFAULT_POLICY = {"name": "round_robin", "max_attempts": 12, "width": 2, "patience": 3}


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _required(mapping: Mapping, key: str, kind: type) -> object:
    value = mapping.get(key)
    if not isinstance(value, kind) or (kind is str and not value):
        raise ValueError("invalid or missing %s" % key)
    return value


def _metric_matches(item: Mapping, metric: Mapping) -> None:
    """Reject optional per-record metric tags that disagree with world metric."""
    if "metric_identity" in item and item["metric_identity"] != metric["identity"]:
        raise ValueError("metric identity mismatch")
    if "metric" in item:
        tagged = item["metric"]
        if not isinstance(tagged, Mapping) or any(tagged.get(k) != metric[k] for k in ("name", "direction", "identity")):
            raise ValueError("metric mismatch")


def validate_world(world: object) -> None:
    """Validate the complete archive before a trusted driver replays it.

    Sequences are globally unique so registration order is outcome-independent.
    Context dependencies may cross branches; they are intentionally not repaired
    here because replay must expose a cross-branch dependency as unsupported when
    a reordered prefix has not revealed it.
    """
    if not isinstance(world, Mapping):
        raise ValueError("world must be a mapping")
    if world.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    _required(world, "world_id", str)
    metric = world.get("metric")
    if not isinstance(metric, Mapping):
        raise ValueError("metric must be a mapping")
    _required(metric, "name", str)
    _required(metric, "identity", str)
    if metric.get("direction") not in {"max", "min"}:
        raise ValueError("metric.direction must be max or min")
    root = world.get("root")
    if not isinstance(root, Mapping) or root.get("id") != "root" or not _number(root.get("score")):
        raise ValueError("root must have id=root and a finite score")
    _metric_matches(root, metric)
    nodes = world.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("nodes must be a list")

    records: dict[str, Mapping] = {"root": root}
    sequences: set[int] = set()
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError("node must be a mapping")
        node_id = _required(node, "id", str)
        if node_id == "root" or node_id in records:
            raise ValueError("node ids must be unique and cannot be root")
        _required(node, "parent_id", str)
        contexts = node.get("context_ids")
        if not isinstance(contexts, list) or not all(isinstance(x, str) and x for x in contexts):
            raise ValueError("context_ids must be a list of ids")
        if len(set(contexts)) != len(contexts) or node_id in contexts:
            raise ValueError("context_ids must be unique and cannot contain self")
        sequence = node.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        if sequence in sequences:
            raise ValueError("sequences must be globally unique")
        sequences.add(sequence)
        status = node.get("status")
        if status not in _STATUSES:
            raise ValueError("invalid status")
        score = node.get("score")
        artifact = node.get("artifact_hash")
        if status == "valid":
            if not _number(score):
                raise ValueError("valid nodes require a finite score")
            if not isinstance(artifact, str) or not artifact:
                raise ValueError("valid nodes require an artifact_hash")
        elif score is not None:
            raise ValueError("invalid/blocked nodes must have null score")
        elif artifact is not None and (not isinstance(artifact, str) or not artifact):
            raise ValueError("artifact_hash must be a string or null")
        if "artifact_hash" not in node:
            raise ValueError("missing artifact_hash")
        if not _number(node.get("cost_seconds")) or node["cost_seconds"] < 0:
            raise ValueError("cost_seconds must be finite and nonnegative")
        _metric_matches(node, metric)
        records[node_id] = node

    child_counts: dict[str, int] = {}
    for node_id, node in records.items():
        if node_id == "root":
            continue
        parent = node["parent_id"]
        if parent not in records:
            raise ValueError("unknown parent_id")
        if any(context not in records for context in node["context_ids"]):
            raise ValueError("unknown context_id")
        child_counts[parent] = child_counts.get(parent, 0) + 1
        if parent != "root" and child_counts[parent] > 1:
            raise ValueError("nonroot nodes may have at most one child")
        if parent != "root" and records[parent]["sequence"] >= node["sequence"]:
            raise ValueError("parent sequence must precede child")
        for context in node["context_ids"]:
            if context != "root" and records[context]["sequence"] >= node["sequence"]:
                raise ValueError("context dependency must precede node")

    for node_id in records:
        seen: set[str] = set()
        current = node_id
        while current != "root":
            if current in seen:
                raise ValueError("parent graph contains a cycle")
            seen.add(current)
            current = records[current]["parent_id"]


def _policy(policy: object) -> dict:
    if policy is None:
        policy = {}
    if not isinstance(policy, Mapping):
        raise ValueError("policy must be a mapping")
    result = dict(_DEFAULT_POLICY)
    result.update(policy)
    if result["name"] not in _POLICIES:
        raise ValueError("unknown policy")
    for key in ("max_attempts", "width", "patience"):
        if not isinstance(result[key], int) or isinstance(result[key], bool) or result[key] < 0:
            raise ValueError("policy.%s must be a nonnegative integer" % key)
    if result["width"] == 0:
        raise ValueError("policy.width must be positive")
    if result["patience"] == 0:
        raise ValueError("policy.patience must be positive")
    if result["name"] == "executable":
        source = result.get("source_file")
        expected = result.get("source_hash")
        timeout = result.get("timeout_seconds", 2)
        if not isinstance(source, str) or not source or not isinstance(expected, str) or not expected:
            raise ValueError("executable policy needs source_file and source_hash")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("executable policy timeout_seconds must be finite and positive")
        path = Path(source)
        if not path.is_file():
            raise ValueError("executable policy source is missing")
        import hashlib
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("executable policy source hash changed")
    return result


def validate_policy(policy: object) -> dict:
    """Validate a policy specification (including executable source pinning)."""
    return _policy(policy)


def policy_observation(observed: Mapping) -> dict:
    """Return the entire policy-visible state, deliberately excluding artifacts/results.

    This is the only object serialized into an executable policy process.  In
    particular it has no workspace paths, evaluator output blobs, sibling
    attempts not already revealed, or archive-level aggregate.
    """
    root = observed["root"]
    metric = observed["metric"]
    nodes = observed.get("nodes", [])
    return {
        "schema_version": 1,
        "kind": "dream_loop_policy_request",
        "root": {"id": root.get("id"), "score": root.get("score")},
        "metric": {key: metric.get(key) for key in ("name", "direction", "identity")},
        "nodes": [{key: node.get(key) for key in
                   ("id", "parent_id", "context_ids", "sequence", "status", "score", "cost_seconds")}
                  for node in nodes],
        "trace": [{key: item.get(key) for key in ("action", "revealed_id", "status")}
                  for item in observed.get("trace", []) if isinstance(item, Mapping)],
    }


def _executable_action(observed: Mapping, policy: Mapping) -> str | None:
    """Invoke a versioned policy source with one JSON request and strict JSON reply.

    Policy sources are trusted local code.  A subprocess bounds wall time and
    gives a narrow I/O contract; it is intentionally not represented as a
    security sandbox.
    """
    request = json.dumps(policy_observation(observed), sort_keys=True, allow_nan=False)
    process = None
    try:
        process = subprocess.Popen([sys.executable, policy["source_file"]], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=(os.name == "posix"))
        stdout, stderr = process.communicate(request, timeout=policy["timeout_seconds"])
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.communicate()
        raise ValueError("executable policy timed out") from exc
    if process.returncode:
        raise ValueError("executable policy failed")
    if len(stdout.encode()) > 100_000:
        raise ValueError("executable policy stdout exceeds 100 KB")
    try:
        reply = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("executable policy must emit one JSON object") from exc
    if not isinstance(reply, Mapping) or set(reply) - {"action"} or "action" not in reply:
        raise ValueError("executable policy reply must be exactly {action}")
    action = reply["action"]
    if action is not None and (not isinstance(action, str) or not action):
        raise ValueError("executable policy action must be a nonempty string or null")
    return action


def _ordered_roots(nodes: list[Mapping]) -> list[Mapping]:
    return sorted((n for n in nodes if n.get("parent_id") == "root"), key=lambda n: (n.get("sequence", 0), nodes.index(n)))


def _branch_id(node_id: str, by_id: dict[str, Mapping]) -> str | None:
    current = node_id
    while current in by_id and by_id[current].get("parent_id") != "root":
        current = by_id[current].get("parent_id")
    return current if current in by_id else None


def _leaves_by_branch(nodes: list[Mapping], roots: list[Mapping]) -> dict[str, Mapping]:
    parents = {n.get("parent_id") for n in nodes}
    by_id = {n.get("id"): n for n in nodes}
    leaves: dict[str, Mapping] = {}
    for node in nodes:
        if node.get("id") not in parents:
            branch = _branch_id(node.get("id"), by_id)
            if branch is not None:
                leaves[branch] = node
    return leaves


def action_is_permitted(observed: Mapping, action: str | None) -> bool:
    """Whether an action names root or an observed node without a child yet."""
    if action == "root":
        return True
    nodes = observed.get("nodes", [])
    if not isinstance(action, str) or not isinstance(nodes, list):
        return False
    ids = {node.get("id") for node in nodes if isinstance(node, Mapping)}
    return action in ids and not any(node.get("parent_id") == action for node in nodes if isinstance(node, Mapping))


def _is_better(score: float, incumbent: float, direction: str) -> bool:
    return score > incumbent if direction == "max" else score < incumbent


def _adaptive_stagnant(nodes: list[Mapping], metric: Mapping, patience: int) -> bool:
    incumbent = metric.get("root_score")
    if not _number(incumbent):
        return False
    stale = 0
    for node in nodes:
        if node.get("status") != "valid" or not _number(node.get("score")):
            continue
        if _is_better(node["score"], incumbent, metric.get("direction")):
            incumbent, stale = node["score"], 0
        else:
            stale += 1
    return stale >= patience


def select_action(observed: object, policy: object) -> str | None:
    """Choose an action using only a revealed prefix, never a complete world.

    ``observed`` has ``root``, ``nodes`` (in reveal order), and ``metric``.  A
    caller may include ``trace`` to make round-robin's next branch explicit;
    without it, the last revealed node supplies the previous branch.
    """
    config = _policy(policy)
    if not isinstance(observed, Mapping):
        raise ValueError("observed must be a mapping")
    nodes = observed.get("nodes", [])
    root = observed.get("root")
    metric = observed.get("metric")
    if not isinstance(nodes, list) or not isinstance(root, Mapping) or not isinstance(metric, Mapping):
        raise ValueError("observed needs root, nodes, and metric")
    if len(nodes) >= config["max_attempts"]:
        return None
    if config["name"] == "executable":
        action = _executable_action(observed, config)
        if action is not None and not action_is_permitted(observed, action):
            raise ValueError("executable policy requested an unavailable action")
        return action
    roots = _ordered_roots(nodes)
    if config["name"] == "explore" or len(roots) < config["width"]:
        return "root"
    if config["name"] == "adaptive":
        adaptive_metric = dict(metric, root_score=root.get("score"))
        if _adaptive_stagnant(nodes, adaptive_metric, config["patience"]):
            return None
    leaves = _leaves_by_branch(nodes, roots)
    if not leaves:
        return "root"
    ordered_ids = [node["id"] for node in roots if node["id"] in leaves]
    if config["name"] in {"greedy", "adaptive"}:
        by_id = {node["id"]: node for node in nodes}
        best_branch, best_score = ordered_ids[0], None
        for branch in ordered_ids:
            for node in nodes:
                if _branch_id(node["id"], by_id) == branch and node.get("status") == "valid" and _number(node.get("score")):
                    score = node["score"]
                    if best_score is None or _is_better(score, best_score, metric.get("direction")):
                        best_branch, best_score = branch, score
        return leaves[best_branch]["id"]
    trace = observed.get("trace", [])
    last = trace[-1].get("action") if trace and isinstance(trace[-1], Mapping) else None
    if not isinstance(last, str) and nodes:
        last = nodes[-1].get("id")
    by_id = {node["id"]: node for node in nodes}
    last_branch = _branch_id(last, by_id) if isinstance(last, str) else None
    index = (ordered_ids.index(last_branch) + 1) % len(ordered_ids) if last_branch in ordered_ids else 0
    return leaves[ordered_ids[index]]["id"]


def replay(world: object, policy: object, deadline: float | None = None) -> dict:
    """Replay one deterministic prefix, charging every revealed recorded attempt."""
    validate_world(world)
    config = _policy(policy)
    metric, root = world["metric"], world["root"]
    children: dict[str, list[Mapping]] = {}
    for node in world["nodes"]:
        children.setdefault(node["parent_id"], []).append(node)
    for values in children.values():
        values.sort(key=lambda n: n["sequence"])
    observed: list[Mapping] = []
    trace: list[dict] = []
    observed_ids = {"root"}
    best = root["score"]
    cost = 0.0
    reason = "policy_stop"
    unsupported = None
    while len(trace) < config["max_attempts"]:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("replay deadline exhausted")
        prefix = {"root": root, "nodes": observed, "metric": metric, "trace": trace}
        invocation_policy = dict(config)
        if deadline is not None and invocation_policy["name"] == "executable":
            invocation_policy["timeout_seconds"] = min(invocation_policy["timeout_seconds"], remaining)
        action = select_action(prefix, invocation_policy)
        if action is None:
            break
        if not action_is_permitted(prefix, action):
            reason, unsupported = "unsupported", "unavailable_action:%s" % action
            break
        choices = [node for node in children.get(action, []) if node["id"] not in observed_ids]
        if not choices:
            reason, unsupported = "unsupported", "no_recorded_continuation:%s" % action
            break
        node = choices[0]
        missing = [context for context in node["context_ids"] if context not in observed_ids]
        if missing:
            reason, unsupported = "unsupported", "missing_context:%s" % missing[0]
            break
        observed.append(node)
        observed_ids.add(node["id"])
        trace.append({"action": action, "revealed_id": node["id"], "status": node["status"],
                      "score": node["score"]})
        cost += node["cost_seconds"]
        if node["status"] == "valid" and _number(node.get("score")) and _is_better(node["score"], best, metric["direction"]):
            best = node["score"]
    improvement = best - root["score"] if metric["direction"] == "max" else root["score"] - best
    result = {"policy": config, "trace": trace, "observed_ids": ["root"] + [node["id"] for node in observed], "stop_reason": reason,
              "attempts": len(trace), "cost_seconds": cost, "best_score": best, "improvement": improvement,
              "metric": dict(metric)}
    if unsupported is not None:
        result["unsupported_reason"] = unsupported
    return result
