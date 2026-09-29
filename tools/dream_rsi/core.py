"""Serial ask/generate/evaluate driver. Commands are trusted, not sandboxed."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid


class HarnessError(ValueError):
    pass


class BudgetExhausted(HarnessError):
    pass


def object_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hash(path, max_bytes=2_000_000):
    """Reject links, large trees and special files; fingerprint relative paths + bytes."""
    path = Path(path)
    h = hashlib.sha256()
    total = 0
    files = sorted(path.rglob("*"))
    if len(files) > 5000:
        raise HarnessError("candidate tree exceeds 5000 entries")
    for entry in files:
        if entry.is_symlink():
            raise HarnessError("candidate snapshots must not contain symlinks")
        if entry.is_dir():
            continue
        if not entry.is_file():
            raise HarnessError("candidate snapshots must contain regular files only")
        total += entry.stat().st_size
        if total > max_bytes:
            raise HarnessError("candidate tree exceeds max_artifact_bytes")
        name = entry.relative_to(path).as_posix().encode()
        h.update(len(name).to_bytes(8, "big") + name)
        data = entry.read_bytes()
        h.update(len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


@contextlib.contextmanager
def lock(run):
    path = Path(run) / ".lock"
    try:
        path.mkdir()
    except FileExistsError:
        raise HarnessError("run is locked; inspect its owner before removing a stale .lock")
    try:
        write_json(path / "owner.json", {"pid": os.getpid(), "time": time.time()})
        yield
    finally:
        (path / "owner.json").unlink(missing_ok=True)
        path.rmdir()


def load_config(path):
    path = Path(path).resolve()
    config = read_json(path)
    for field in ("name", "question", "metric", "seed_dir", "evaluate", "verifier_files", "limits", "policy"):
        if field not in config:
            raise HarnessError(f"missing config field: {field}")
    metric = config["metric"]
    if metric.get("direction") not in ("min", "max") or not all(isinstance(metric.get(k), str) and metric[k] for k in ("name", "identity")):
        raise HarnessError("metric needs name, identity and direction=min|max")
    for key, default in (("attempts", 12), ("wall_seconds", 300), ("command_seconds", 30), ("max_artifact_bytes", 2_000_000)):
        value = config["limits"].setdefault(key, default)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise HarnessError(f"limits.{key} must be finite and positive")
    if not isinstance(config["limits"]["attempts"], int):
        raise HarnessError("attempt limit must be an integer")
    for key in ("evaluate", "generate"):
        command = config.get(key)
        if key == "generate" and command is None:
            continue
        if not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
            raise HarnessError(f"{key} must be an argv array, not a shell string")
        config[key] = [s.replace("{python}", sys.executable).replace("{config_dir}", str(path.parent)) for s in command]
    config["seed_dir"] = str((path.parent / config["seed_dir"]).resolve())
    if not isinstance(config["verifier_files"], list) or not config["verifier_files"]:
        raise HarnessError("declare at least one verifier file to fingerprint")
    config["verifier_files"] = [str((path.parent / p).resolve()) for p in config["verifier_files"]]
    generator_files = config.get("generator_files", [])
    if not isinstance(generator_files, list) or not all(isinstance(p, str) and p for p in generator_files):
        raise HarnessError("generator_files must be an array of nonempty paths")
    config["generator_files"] = [str((path.parent / p).resolve()) for p in generator_files]
    config["policy"] = dict(config["policy"], max_attempts=config["limits"]["attempts"])
    return config


def freeze_policy(policy, run):
    """Copy an executable policy into a run and pin its content digest.

    The copied source is what both live decisions and later replay execute.  A
    policy is trusted code, but it cannot silently change under a saved run.
    """
    policy = dict(policy)
    if policy.get("name") != "executable":
        return policy
    source = Path(policy.get("source_file", ""))
    expected = policy.get("source_hash")
    if not source.is_file() or not isinstance(expected, str) or digest(source) != expected:
        raise HarnessError("executable policy source is missing or changed before freezing")
    destination = Path(run) / "policy" / "selected.py"
    destination.parent.mkdir(exist_ok=True)
    shutil.copyfile(source, destination)
    policy["source_file"] = str(destination)
    policy["source_hash"] = digest(destination)
    return policy


def assert_verifier(state):
    if object_hash(state["config"]) != state["config_hash"]:
        raise HarnessError("frozen project config changed")
    if state["metric"] != state["config"]["metric"]:
        raise HarnessError("world metric differs from frozen project config")
    for name, expected in state["harness_hashes"].items():
        if digest(Path(__file__).parent / name) != expected:
            raise HarnessError(f"harness code changed since run creation: {name}; keep the old environment or start a new run")
    for path, expected in state["verifier_hashes"].items():
        if not Path(path).is_file() or digest(path) != expected:
            raise HarnessError(f"fixed verifier changed: {path}")
    for path, expected in state.get("generator_hashes", {}).items():
        if not Path(path).is_file() or digest(path) != expected:
            raise HarnessError(f"fixed generator changed: {path}")
    from .replay import validate_policy
    try:
        validate_policy(state["config"]["policy"])
    except ValueError as exc:
        raise HarnessError(f"frozen policy changed or invalid: {exc}") from exc


def command(argv, cwd, folder, phase, timeout, packet=None):
    """Run without a shell; terminate this command's process group on a POSIX timeout."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["DREAM_WORKSPACE"] = str(cwd)
    if packet:
        env["DREAM_PACKET"] = str(packet)
    start = time.monotonic()
    out = Path(folder) / f"{phase}.stdout"
    err = Path(folder) / f"{phase}.stderr"
    timed_out = False
    with out.open("wb") as stdout, err.open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                   start_new_session=(os.name == "posix"))
        write_json(Path(folder) / f"{phase}.started.json", {"pid": process.pid, "started_at": time.time()})
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
    info = {"argv": argv, "elapsed_seconds": time.monotonic() - start,
            "returncode": process.returncode, "timed_out": timed_out}
    write_json(Path(folder) / f"{phase}.command.json", info)
    if timed_out:
        raise BudgetExhausted(f"{phase} command deadline exhausted; see {err}")
    if process.returncode:
        raise HarnessError(f"{phase} failed; see {err}")
    if out.stat().st_size > 1_000_000:
        raise HarnessError(f"{phase} stdout exceeds 1 MB")
    return out.read_text(encoding="utf-8"), info["elapsed_seconds"]


def remaining(state):
    return state["deadline_at"] - time.time()


def evaluate(state, workspace, folder):
    assert_verifier(state)
    limits = state["config"]["limits"]
    before = tree_hash(workspace, limits["max_artifact_bytes"])
    timeout = min(limits["command_seconds"], remaining(state))
    if timeout <= 0:
        raise BudgetExhausted("run wall deadline exhausted")
    text, seconds = command(state["config"]["evaluate"], workspace, folder, "evaluate", timeout)
    assert_verifier(state)
    if before != tree_hash(workspace, limits["max_artifact_bytes"]):
        raise HarnessError("evaluator changed candidate files; comparison is invalid")
    result = json.loads(text)
    status = result.get("status")
    score = result.get("score")
    if result.get("metric") != state["metric"]:
        raise HarnessError("evaluator metric contract mismatch")
    if status not in ("valid", "invalid", "blocked"):
        raise HarnessError("evaluator status must be valid, invalid or blocked")
    if status == "valid" and (isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)):
        raise HarnessError("valid evaluations require a finite numeric score")
    if status != "valid" and score is not None:
        raise HarnessError("invalid/blocked evaluations must have null score")
    return result, seconds, before


def initialize(config_path, run, policy=None, wall_seconds=None):
    config = load_config(config_path)
    if wall_seconds is not None:
        if not isinstance(wall_seconds, (int, float)) or isinstance(wall_seconds, bool) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
            raise HarnessError("wall_seconds override must be finite and positive")
        config["limits"]["wall_seconds"] = min(config["limits"]["wall_seconds"], wall_seconds)
    if policy:
        config["policy"] = dict(policy, max_attempts=config["limits"]["attempts"])
    # Validate policy before creating files.  Do not execute untrusted policy
    # code during initialization: its first real invocation belongs to a run's
    # bounded decision budget.
    from .replay import validate_policy
    validate_policy(config["policy"])
    run = Path(run).resolve()
    if run.exists():
        raise HarnessError("output already exists; use run/ask to continue, never overwrite")
    seed = Path(config["seed_dir"])
    tree_hash(seed, config["limits"]["max_artifact_bytes"])
    if not seed.is_dir():
        raise HarnessError("seed_dir is not a directory")
    if seed == run or seed in run.parents:
        raise HarnessError("output must be outside seed_dir")
    verifier = {p: digest(p) for p in config["verifier_files"]}
    generators = {p: digest(p) for p in config["generator_files"]}
    run.mkdir(parents=True)
    (run / "attempts").mkdir()
    config["policy"] = freeze_policy(config["policy"], run)
    started = time.time()
    state = {"schema_version": 1, "world_id": str(uuid.uuid4()), "metric": config["metric"],
             "config": config, "config_hash": object_hash(config), "verifier_hashes": verifier,
             "generator_hashes": generators, "started_at": started,
             "deadline_at": started + config["limits"]["wall_seconds"], "nodes": []}
    state["harness_hashes"] = {name: digest(Path(__file__).parent / name) for name in ("core.py", "replay.py", "cli.py", "evolve.py")}
    snapshot = run / "harness"
    snapshot.mkdir()
    for name in state["harness_hashes"]:
        shutil.copyfile(Path(__file__).parent / name, snapshot / name)
    root = run / "root"
    root.mkdir()
    shutil.copytree(seed, root / "workspace")
    try:
        result, seconds, artifact = evaluate(state, root / "workspace", root)
        if result["status"] != "valid":
            raise HarnessError("seed must pass the evaluator before search")
    except Exception as exc:
        write_json(run / "initialization-failure.json", {"error": str(exc)})
        raise
    state["root"] = {"id": "root", "score": result["score"], "artifact_hash": artifact,
                     "cost_seconds": seconds, "result": result}
    write_json(run / "world.json", state)
    return state


def workspace_path(run, node_id):
    return Path(run) / ("root" if node_id == "root" else f"attempts/{node_id}") / "workspace"


def node_by_id(state, node_id):
    if node_id == "root":
        return state["root"]
    return next(n for n in state["nodes"] if n["id"] == node_id)


def verify_artifacts(run, state):
    assert_verifier(state)
    for node in [state["root"], *state["nodes"]]:
        if node.get("artifact_hash") and tree_hash(workspace_path(run, node["id"]), state["config"]["limits"]["max_artifact_bytes"]) != node["artifact_hash"]:
            raise HarnessError(f"retained artifact changed: {node['id']}")


def ask(run, automated=False):
    from .replay import select_action
    run = Path(run).resolve()
    with lock(run):
        state = verify(run)
        if (run / "pending.json").exists():
            raise HarnessError("one attempt is pending; evaluate or abandon it first")
        if remaining(state) <= 0:
            return None
        observed = {"root": state["root"], "nodes": state["nodes"], "metric": state["metric"],
                    "trace": [{"action": node["parent_id"], "revealed_id": node["id"], "status": node["status"]}
                              for node in state["nodes"]]}
        policy = dict(state["config"]["policy"])
        if policy.get("name") == "executable":
            policy["timeout_seconds"] = min(policy["timeout_seconds"], remaining(state))
        parent = select_action(observed, policy)
        if parent is None:
            return None
        if remaining(state) <= 0:
            return None
        sequence = len(state["nodes"]) + 1
        node_id = f"n{sequence:04d}"
        folder = run / "attempts" / node_id
        folder.mkdir()  # A partial old attempt must not be silently overwritten.
        lineage = []
        cursor = parent
        while cursor != "root":
            n = node_by_id(state, cursor)
            lineage.append(n)
            cursor = n["parent_id"]
        lineage.reverse()
        # An executable policy's action is its exact parent.  Only an invalid or
        # blocked parent needs the explicit best-valid-lineage fallback.
        selected_parent = node_by_id(state, parent)
        if selected_parent.get("status", "valid") == "valid":
            anchor = parent
        else:
            retained = [state["root"]] + [n for n in lineage if n["status"] == "valid"]
            best = (max if state["metric"]["direction"] == "max" else min)(retained, key=lambda n: n["score"])
            anchor = best["id"]
        shutil.copytree(workspace_path(run, anchor), folder / "workspace")
        # Deliberately omit sibling outcomes, archive size, and the global incumbent.
        branch = (sum(n["parent_id"] == "root" for n in state["nodes"]) + 1 if not lineage
                  else read_json(run / "attempts" / lineage[0]["id"] / "packet.json")["branch"])
        packet = {"schema_version": 1, "attempt_id": node_id, "sequence": sequence,
                  "branch": branch, "depth": len(lineage) + 1,
                  "question": state["config"]["question"], "metric": state["metric"],
                  "parent_id": parent, "artifact_parent_id": anchor,
                  "workspace": str(folder / "workspace"), "root": state["root"],
                  "lineage": lineage, "instructions": "Improve the candidate in workspace. Read only this packet and its workspace; do not inspect sibling attempts. Keep evaluator fixed. Write proposal.md in workspace explaining the mechanism. No claim is a result until evaluated."}
        write_json(folder / "packet.json", packet)
        pending = {"id": node_id, "parent_id": parent, "artifact_parent_id": anchor,
                   "sequence": sequence, "context_ids": ["root"] + [n["id"] for n in lineage],
                   "packet_hash": digest(folder / "packet.json"), "created_at": time.time(),
                   "automated": automated, "owner_pid": os.getpid() if automated else None}
        write_json(run / "pending.json", pending)
        write_json(folder / "decision.json", {**pending, "policy": state["config"]["policy"],
                    "observed_ids": ["root"] + [n["id"] for n in state["nodes"]]})
        return packet


def _finish(run, state, pending, result, seconds, artifact):
    node = {**pending, "status": result["status"], "score": result.get("score"),
            "cost_seconds": seconds, "artifact_hash": artifact, "result": result,
            "completed_at": time.time()}
    state["nodes"].append(node)
    write_json(run / "attempts" / pending["id"] / "result.json", node)
    write_json(run / "world.json", state)
    (run / "pending.json").unlink()
    return node


def tell(run, abandoned=None, _automatic=False):
    run = Path(run).resolve()
    with lock(run):
        state = verify(run)
        pending = read_json(run / "pending.json")
        if pending.get("automated") and not _automatic and abandoned is None:
            raise HarnessError("automatic generator owns this attempt; do not evaluate it concurrently")
        if pending.get("automated") and not _automatic and abandoned is not None:
            folder = run / "attempts" / pending["id"]
            pids = [pending.get("owner_pid")]
            for phase in ("generate", "evaluate"):
                started_path = folder / f"{phase}.started.json"
                if started_path.exists():
                    pids.append(read_json(started_path)["pid"])
            for pid in pids:
                if pid:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        continue
                    except PermissionError:
                        pass
                    raise HarnessError("automatic attempt may still be running; reconcile its owner/command before abandoning")
        if any(n["id"] == pending["id"] for n in state["nodes"]):
            # Atomic world commit succeeded but pending cleanup was interrupted.
            (run / "pending.json").unlink()
            return node_by_id(state, pending["id"])
        folder = run / "attempts" / pending["id"]
        if digest(folder / "packet.json") != pending["packet_hash"]:
            raise HarnessError("decision packet was modified")
        started = folder / "evaluation-started.json"
        if started.exists() and abandoned is None:
            raise HarnessError("evaluation was already started; inspect outputs then abandon, never automatically duplicate")
        if not abandoned:
            write_json(started, {"started_at": time.time()})
        begin = time.monotonic()
        artifact = None
        try:
            if abandoned:
                result = {"status": "blocked", "score": None, "summary": abandoned}
            else:
                result, _, artifact = evaluate(state, folder / "workspace", folder)
        except BudgetExhausted as exc:
            result = {"status": "blocked", "score": None, "summary": str(exc)}
        except (ValueError, OSError) as exc:
            result = {"status": "invalid", "score": None, "summary": str(exc)}
        # Keep invalid candidate evidence too, when its tree meets the resource contract.
        if artifact is None:
            try:
                artifact = tree_hash(folder / "workspace", state["config"]["limits"]["max_artifact_bytes"])
            except HarnessError:
                pass
        generation = folder / "generate.command.json"
        generation_seconds = read_json(generation)["elapsed_seconds"] if generation.exists() else 0
        result["generation_cost_scope"] = "measured_command" if generation.exists() else "external_unmeasured"
        return _finish(run, state, pending, result, time.monotonic() - begin + generation_seconds, artifact)


def run_automatic(run):
    run = Path(run).resolve()
    state = read_json(run / "world.json")
    if not state["config"].get("generate"):
        raise HarnessError("no generate command configured; use ask/evaluate with your existing agent")
    while True:
        packet = ask(run, automated=True)
        if packet is None:
            break
        folder = run / "attempts" / packet["attempt_id"]
        failure = None
        try:
            timeout = min(state["config"]["limits"]["command_seconds"], remaining(state))
            if timeout <= 0:
                raise HarnessError("run wall deadline exhausted before generation")
            command(state["config"]["generate"], folder / "workspace", folder, "generate", timeout, folder / "packet.json")
        except (ValueError, OSError) as exc:
            failure = f"generation failed: {exc}"
        node = tell(run, abandoned=failure, _automatic=True)
        yield node


def verify(run):
    from .replay import validate_world
    run = Path(run).resolve()
    state = read_json(run / "world.json")
    validate_world(state)
    verify_artifacts(run, state)
    for node in state["nodes"]:
        folder = run / "attempts" / node["id"]
        if read_json(folder / "result.json") != node:
            raise HarnessError(f"result record mismatch: {node['id']}")
        if digest(folder / "packet.json") != node["packet_hash"]:
            raise HarnessError(f"packet record mismatch: {node['id']}")
    return state
