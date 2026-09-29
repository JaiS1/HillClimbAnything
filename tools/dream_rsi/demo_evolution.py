"""One-command CPU demonstration of policy revision and live redeployment."""
import argparse
from pathlib import Path
import sys

from . import core
from .cli import cycle, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    if root.exists():
        parser.error("choose a fresh output directory")
    home = Path(__file__).resolve().parent
    print("CPU protocol demonstration; scripted developer, no model calls.", flush=True)
    runs = cycle(home / "examples/knapsack/project.json", root,
                 [sys.executable, str(home / "examples/policy_developer.py")],
                 cycles=2, revisions=2, developer_seconds=5, policy_seconds=2, wall_seconds=180,
                 penalty=0.5)
    lines = ["# Dream Loop: executable policy evolution", "",
             "CPU integration demonstration with a deterministic policy developer.",
             "This proves execution and redeployment, not LLM policy learning or prospective gains.", "",
             "| Rollout | Attempts | Seed | Best | Policy |",
             "| --- | ---: | ---: | ---: | --- |"]
    for run in runs:
        world = core.verify(run)
        scores = [world["root"]["score"]] + [n["score"] for n in world["nodes"] if n["status"] == "valid"]
        best = (max if world["metric"]["direction"] == "max" else min)(scores)
        lines.append(f"| [{run.name}]({run.name}/report.md) | {len(world['nodes'])} | "
                     f"{world['root']['score']} | {best} | {world['config']['policy']['name']} |")
        write_report(run)
    for path in sorted(root.glob("dream-*.json")):
        result = core.read_json(path)
        lines += ["", f"## {path.stem}", "",
                  f"Initial replay utility: {result['initial_evaluation']['utility']}",
                  f"Selected replay utility: {result['selected_evaluation']['utility']}",
                  f"Selected source SHA256: `{result['selected_source_hash']}`", ""]
        for revision in result["revisions"]:
            paired = revision.get("paired")
            check = (f"{paired['verdict']} ({'; '.join(paired['reasons']) or 'guardrails hold'})"
                     if paired else "not computed (a policy was unsupported or the revision failed)")
            lines.append(f"- Revision {revision['revision']}: {'accepted' if revision['accepted'] else 'rejected'} "
                         f"by the `{result['promotion']}` rule; paired noise check: {check}")
        lines += ["",
                  "The demo pools one recorded world, so the paired check cannot separate a gain from noise.",
                  "With `--promotion paired`, such revisions are held rather than promoted.", "",
                  f"[Selection and feedback]({path.name}) · [Versioned policy sources]({path.stem}.policies/)",
                  "", "The next live rollout uses the selected source. Both rollouts use the same toy problem;",
                  "this is not a held-out research evaluation."]
    report = root / "report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Completed {len(runs)} rollouts. Report: {report}")


if __name__ == "__main__":
    main()
