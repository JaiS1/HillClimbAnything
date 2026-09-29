"""Small CLI: one project, one evaluator, a finite search, and inspectable dreams."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from . import core
from . import evolve as evolution
from . import stats
from .replay import replay


def best_node(world):
    candidates = [world["root"]] + [n for n in world["nodes"] if n["status"] == "valid"]
    return (max if world["metric"]["direction"] == "max" else min)(candidates, key=lambda n: n["score"])


def dream(runs, penalty=0.0):
    """Finite rule search, not LLM policy-code evolution or an unbiased OPE estimate."""
    if not math.isfinite(penalty) or penalty < 0:
        raise core.HarnessError("attempt penalty must be finite and nonnegative")
    worlds = [core.verify(run) for run in runs]
    first = worlds[0]
    if any(w["metric"] != first["metric"] or w["verifier_hashes"] != first["verifier_hashes"] for w in worlds):
        raise core.HarnessError("dream pool mixes evaluator or metric identities")
    if len({w["world_id"] for w in worlds}) != len(worlds):
        raise core.HarnessError("duplicate world in dream pool")
    policies = [dict(first["config"]["policy"])]
    cap = min(w["config"]["limits"]["attempts"] for w in worlds)
    policies[0]["max_attempts"] = cap
    for name in ("round_robin", "greedy", "explore", "adaptive"):
        for width in (1, 2, 3):
            policy = {"name": name, "width": width, "patience": 3, "max_attempts": cap}
            if policy not in policies:
                policies.append(policy)
    results = []
    for policy in policies:
        episodes = [replay(w, policy) for w in worlds]
        supported = all(r["stop_reason"] == "policy_stop" for r in episodes)
        utility = (sum(r["improvement"] - penalty * r["attempts"] for r in episodes) / len(episodes)) if supported else None
        results.append({"policy": policy, "eligible": supported, "utility": utility,
                        "episodes": episodes})
    eligible = [r for r in results if r["eligible"]]
    # Stable tie-break retains the incumbent, then declaration order.
    selected = max(eligible, key=lambda r: r["utility"]) if eligible else None
    return {"schema_version": 1, "kind": "in_sample_replay_selection", "metric": first["metric"],
            "verifier_hashes": first["verifier_hashes"], "attempt_penalty": penalty,
            "objective": "mean(direction-adjusted gain - attempt_penalty * revealed attempts)",
            "source_worlds": [{"id": w["world_id"], "file": str(Path(p).resolve() / "world.json"),
                                "sha256": core.digest(Path(p) / "world.json")} for p, w in zip(runs, worlds)],
            "policies_tested": len(results), "selected_policy": selected["policy"] if selected else None,
            "results": results,
            "claim": "Conditional fit to recorded worlds. Unsupported tails excluded. No claim of prospective improvement."}


def write_report(run):
    run = Path(run).resolve()
    world = core.verify(run)
    best = best_node(world)
    lines = [f"# Dream Loop · {world['config']['name']}", "",
             "Dream-RSI-inspired project hill climbing. Recorded evidence, finite budget.", "",
             f"**Best:** {best['score']:g} · **Seed:** {world['root']['score']:g} · "
             f"**Attempts:** {len(world['nodes'])}/{world['config']['limits']['attempts']}", "",
             f"Metric: `{world['metric']['name']}` ({world['metric']['direction']}); identity `{world['metric']['identity']}`.",
             f"Question: {world['config']['question']}", "",
             f"Retained best: `{core.workspace_path(run, best['id'])}`", "",
             "## Recorded search", "",
             "| Attempt | Search parent | Artifact parent | Outcome | Score | Command seconds |",
             "| --- | --- | --- | --- | ---: | ---: |"]
    for n in world["nodes"]:
        score = "—" if n["score"] is None else f"{n['score']:g}"
        lines.append(f"| {n['id']} | {n['parent_id']} | {n['artifact_parent_id']} | {n['status']} | {score} | {n['cost_seconds']:.3f} |")
    lines += ["", "Scientific interpretation is task-specific: a valid score is not an independently confirmed research result.",
              "Command seconds include supervised generation/evaluation; manual agent work is explicitly unmeasured.",
              "The wall deadline includes time between manual handoffs. Baseline evaluation is charged separately in root metadata."]
    selection = run / "dream.json"
    if selection.exists():
        result = core.read_json(selection)
        sources = result["source_worlds"]
        current_hash = core.digest(run / "world.json")
        stale = any(s["file"] == str(run / "world.json") and s["sha256"] != current_hash for s in sources)
        lines += ["", "## Dream: policy comparison", "", result["claim"], "",
                  f"Policy trials: {result['policies_tested']}. Attempt penalty: {result['attempt_penalty']:g}.",
                  f"Snapshot status: {'STALE: search continued after dreaming' if stale else 'recorded source snapshot'}.", "",
                  "| Policy | Width | Supported pool | Replay utility |", "| --- | ---: | --- | ---: |"]
        for r in result["results"]:
            value = f"{r['utility']:.3f}" if r["eligible"] else "unsupported"
            lines.append(f"| {r['policy']['name']} | {r['policy']['width']} | {'yes' if r['eligible'] else 'no'} | {value} |")
        lines += ["", f"Next-run recommendation: `{json.dumps(result['selected_policy'], sort_keys=True)}`.",
                  "This policy stays frozen during its next live run. Replay cannot assign outcomes to untried branches."]
    lines += ["", "## Boundaries", "", "No leaderboard ranking, autonomous mathematics result, or provider endorsement is claimed by this demo.",
              "Commands run with the invoking user's privileges. Workspace copies are not a security sandbox.", ""]
    path = run / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def checked_selection(path, config, deadline=None):
    selected = core.read_json(path)
    if selected.get("kind") not in {"in_sample_replay_selection", "policy_code_evolution_v1"}:
        raise core.HarnessError("policy file is not a dream selection")
    if selected["metric"] != config["metric"] or selected["verifier_hashes"] != {p: core.digest(p) for p in config["verifier_files"]}:
        raise core.HarnessError("selected policy belongs to a different metric/evaluator")
    for source in selected["source_worlds"]:
        if core.digest(source["file"]) != source["sha256"]:
            raise core.HarnessError("dream source world changed; create a new selection")
    if selected["kind"] == "in_sample_replay_selection":
        expected = dream([Path(s["file"]).parent for s in selected["source_worlds"]], selected["attempt_penalty"])
        if selected != expected:
            raise core.HarnessError("selection does not reproduce from its recorded worlds")
        if selected["selected_policy"] is None:
            raise core.HarnessError("no supported policy was selected")
        return selected
    policy = evolution.materialize_selected_source(path, selected)
    if selected.get("generator_hashes") != {p: core.digest(p) for p in config.get("generator_files", [])}:
        raise core.HarnessError("evolved policy belongs to a changed generator source")
    if selected.get("project_contract") != {"evaluate": config.get("evaluate"), "generate": config.get("generate")}:
        raise core.HarnessError("evolved policy belongs to a different generator or evaluator command")
    worlds = [core.verify(Path(s["file"]).parent) for s in selected["source_worlds"]]
    rescored = evolution.score_policy(worlds, policy, selected["attempt_penalty"], deadline=deadline)
    if not evolution.equivalent_score(rescored, selected.get("selected_evaluation")):
        raise core.HarnessError("evolved selection does not reproduce from its recorded worlds")
    if not rescored["eligible"]:
        raise core.HarnessError("selected executable policy is not supported on its recorded worlds")
    selected["selected_policy"] = policy
    return selected


def cycle(config, output, developer, cycles, revisions, developer_seconds, policy_seconds, wall_seconds, penalty,
          promotion="point", rule=None):
    """Close the serial loop: rollout, evolve policy code, adopt, repeat."""
    if cycles < 1 or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise core.HarnessError("cycles and cycle wall deadline must be positive")
    output = Path(output).resolve()
    if output.exists():
        raise core.HarnessError("cycle output already exists")
    output.mkdir(parents=True)
    started = __import__("time").monotonic()
    runs, policy_source = [], None
    for number in range(1, cycles + 1):
        if __import__("time").monotonic() - started >= wall_seconds:
            break
        run = output / ("rollout-%03d" % number)
        policy = None
        if policy_source:
            selection = checked_selection(policy_source, core.load_config(config), deadline=started + wall_seconds)
            policy = selection["selected_policy"]
        core.initialize(config, run, policy, wall_seconds=max(0.01, wall_seconds - (__import__("time").monotonic() - started)))
        list(core.run_automatic(run))
        runs.append(run)
        remaining = wall_seconds - (__import__("time").monotonic() - started)
        if number == cycles or remaining <= 0:
            break
        selection_path = output / ("dream-%03d.json" % number)
        evolution.evolve(runs, developer, revisions=revisions, timeout_seconds=developer_seconds, policy_seconds=policy_seconds,
                         wall_seconds=remaining, penalty=penalty, output=selection_path,
                         promotion=promotion, rule=rule)
        policy_source = selection_path
    return runs


def developer_argv(path):
    value = core.read_json(path)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise core.HarnessError("developer JSON must contain one nonempty argv string array")
    return value


RULE_FLAGS = (("alpha", float), ("min_units", int), ("bootstrap", int), ("seed", int), ("harm_tolerance", float),
              ("max_harm_rate", float), ("max_subgroup_loss", float), ("useful_effect", float), ("comparisons", int))


def add_rule_flags(parser, promotion):
    if promotion:
        parser.add_argument("--promotion", choices=("point", "paired"), default="point",
                            help="point: higher mean replay utility; paired: corrected paired interval must exclude zero")
    for name, kind in RULE_FLAGS:
        parser.add_argument("--" + name.replace("_", "-"), type=kind, help=f"decision rule {name} (default {stats.DEFAULT_RULE[name]})")


def rule_from(args):
    return {name: getattr(args, name) for name, _ in RULE_FLAGS if getattr(args, name) is not None}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dream-loop", description="Dream-RSI-inspired project hill climbing under a fixed budget.")
    subs = parser.add_subparsers(dest="command", required=True)
    demo_parser = subs.add_parser("demo", help="run the zero-key, CPU-only knapsack search and replay comparison")
    demo_parser.add_argument("--output", required=True)
    init = subs.add_parser("init", help="freeze a project contract and evaluate its seed")
    init.add_argument("config")
    init.add_argument("--output", required=True)
    init.add_argument("--policy-from", help="use a policy selected on earlier recorded worlds")
    for name in ("run", "ask", "evaluate", "status", "report", "verify", "abandon"):
        sub = subs.add_parser(name)
        sub.add_argument("run")
        if name == "abandon":
            sub.add_argument("--reason", required=True)
    sub = subs.add_parser("dream", help="compare a finite policy grid on recorded worlds")
    sub.add_argument("runs", nargs="+")
    sub.add_argument("--attempt-penalty", type=float, default=0.0)
    sub.add_argument("--output", required=True)
    sub = subs.add_parser("evolve", help="iteratively improve versioned executable policy code using replay feedback")
    sub.add_argument("runs", nargs="+")
    sub.add_argument("--developer-json", required=True, help="JSON file containing trusted developer argv")
    sub.add_argument("--revisions", type=int, default=4)
    sub.add_argument("--developer-seconds", type=float, default=10)
    sub.add_argument("--policy-seconds", type=float, default=2)
    sub.add_argument("--wall-seconds", type=float, default=60)
    sub.add_argument("--attempt-penalty", type=float, default=0.0)
    sub.add_argument("--initial-policy", help="optional initial executable Python source")
    sub.add_argument("--confirm-runs", nargs="+", default=[], help="held-out recorded runs used only to confirm the final selection")
    add_rule_flags(sub, promotion=True)
    sub.add_argument("--output", required=True)
    sub = subs.add_parser("cycle", help="run serial rollouts and policy-code evolution under one outer deadline")
    sub.add_argument("config")
    sub.add_argument("--output", required=True)
    sub.add_argument("--developer-json", required=True, help="JSON file containing trusted developer argv")
    sub.add_argument("--cycles", type=int, default=2)
    sub.add_argument("--revisions", type=int, default=4)
    sub.add_argument("--developer-seconds", type=float, default=10)
    sub.add_argument("--policy-seconds", type=float, default=2)
    sub.add_argument("--wall-seconds", type=float, default=300)
    sub.add_argument("--attempt-penalty", type=float, default=0.0)
    add_rule_flags(sub, promotion=True)
    sub = subs.add_parser("register", help="freeze arms, decision rule, premise check and power target before results")
    sub.add_argument("draft")
    sub.add_argument("--output", required=True)
    sub = subs.add_parser("compare", help="paired, noise-aware promotion verdict from per-item score files")
    sub.add_argument("candidates", nargs="+")
    sub.add_argument("--incumbent", required=True)
    sub.add_argument("--control", help="the incumbent re-run in the candidates' session and runtime")
    sub.add_argument("--registration", help="frozen registration; its rule replaces all rule flags")
    add_rule_flags(sub, promotion=False)
    sub.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            config = Path(__file__).parent / "examples/knapsack/project.json"
            world = core.initialize(config, args.output)
            print(f"DREAM LOOP  |  {world['config']['name']}\nSeed: {world['root']['score']:g}")
            for node in core.run_automatic(args.output):
                print(f"  {node['id']} <- {node['parent_id']:<5}  {node['status']:<7}  score={node['score']}")
            core.write_json(Path(args.output) / "dream.json", dream([args.output], 0.5))
            print(f"\nReport: {write_report(args.output)}")
        elif args.command == "init":
            policy = None
            if args.policy_from:
                config = core.load_config(args.config)
                selected = checked_selection(args.policy_from, config)
                policy = selected["selected_policy"]
            world = core.initialize(args.config, args.output, policy)
            if args.policy_from:
                core.write_json(Path(args.output) / "policy-source.json", {"path": str(Path(args.policy_from).resolve()), "sha256": core.digest(args.policy_from), "selection": selected})
            print(f"Initialized {args.output}; seed={world['root']['score']:g}")
        elif args.command == "run":
            for node in core.run_automatic(args.run):
                print(f"{node['id']}: {node['status']}, score={node['score']}")
            print(write_report(args.run))
        elif args.command == "ask":
            packet = core.ask(args.run)
            print(json.dumps(packet, indent=2) if packet else "Stopped: policy or wall budget exhausted.")
        elif args.command in ("evaluate", "abandon"):
            node = core.tell(args.run, abandoned=args.reason if args.command == "abandon" else None)
            print(json.dumps(node, indent=2))
        elif args.command == "dream":
            output = Path(args.output)
            if output.exists():
                raise core.HarnessError("dream output already exists; choose a new path to preserve selection history")
            core.write_json(output, dream(args.runs, args.attempt_penalty))
            print(output)
        elif args.command == "evolve":
            result = evolution.evolve(args.runs, developer_argv(args.developer_json), revisions=args.revisions,
                                      timeout_seconds=args.developer_seconds, policy_seconds=args.policy_seconds, wall_seconds=args.wall_seconds,
                                      penalty=args.attempt_penalty, output=args.output,
                                      initial_source=args.initial_policy, promotion=args.promotion,
                                      rule=rule_from(args), confirm_runs=args.confirm_runs)
            print(f"{args.output}: selected {result['selected_source_hash'][:12]}")
            if result["confirmation"]:
                print(f"Confirmation on held-out worlds: {result['confirmation']['verdict']}"
                      + (" (reverted to the initial policy)" if result["confirmation"]["reverted_to_initial"] else ""))
        elif args.command == "cycle":
            runs = cycle(args.config, args.output, developer_argv(args.developer_json), args.cycles, args.revisions,
                         args.developer_seconds, args.policy_seconds, args.wall_seconds, args.attempt_penalty,
                         promotion=args.promotion, rule=rule_from(args))
            print("\n".join(str(run) for run in runs))
        elif args.command in ("register", "compare"):
            output = Path(args.output)
            if output.exists():
                raise core.HarnessError("output already exists; choose a new path to preserve the record")
            if args.command == "register":
                frozen = stats.register(core.read_json(args.draft))
                core.write_json(output, frozen)
                print(f"Registered {frozen['sha256'][:12]} at {frozen['registered_utc']}")
                for warning in frozen["warnings"]:
                    print(f"warning: {warning}")
            else:
                result = stats.compare([stats.load_scores(p) for p in args.candidates], stats.load_scores(args.incumbent),
                                       stats.load_scores(args.control) if args.control else None,
                                       stats.load_frozen(args.registration) if args.registration else None,
                                       rule_from(args))
                core.write_json(output, result)
                print(stats.render(result))
        elif args.command == "report":
            print(write_report(args.run))
        else:
            world = core.verify(args.run)
            best = best_node(world)
            print(f"Verified {len(world['nodes'])} attempts; best={best['score']:g} ({best['id']}); remaining wall seconds={max(0,core.remaining(world)):.1f}")
    except (ValueError, OSError, KeyError, StopIteration) as exc:
        parser.exit(2, f"dream-loop: {exc}\n")


if __name__ == "__main__":
    main()
