"""Independent check of the showcase result, outside HCA's measurement path (CLIMB-PLAN S7).

Third corpus: Python-Markdown's own test fixtures (tests/**/*.txt), never measured during the climb,
concatenated per directory into larger documents. Two git worktrees: A = the 3.11.1 tag, B = the
final confirmed commit. For ROUNDS rounds, in a random order per round, a fresh process per arm
renders every document once after a warm-up and prints its total render time. Output equality
(A vs B HTML per document) is checked once. The paired per-round log ratios are tested with the same
exact signed-rank test HCA uses, but nothing else from HCA is involved.

  python independent_check.py <repo> <commit-B> [rounds]
"""

import hashlib
import json
import math
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the HCA repo root
from hca import stats  # noqa: E402

PY = sys.executable  # run this script with the Python that has the project installed
EXTENSIONS = ["markdown.extensions.extra", "markdown.extensions.toc", "markdown.extensions.admonition",
              "markdown.extensions.sane_lists", "markdown.extensions.smarty", "markdown.extensions.meta",
              "markdown.extensions.wikilinks"]

WORKER = r"""
import json, sys, time, hashlib
sys.path.insert(0, sys.argv[1])
import markdown
assert markdown.__file__.startswith(sys.argv[1]), markdown.__file__
docs = json.load(open(sys.argv[2]))
ext = json.loads(sys.argv[3])
markdown.Markdown(extensions=ext).convert(open(sys.argv[4]).read())      # warm-up
total, hashes = 0.0, {}
for name in sorted(docs):
    start = time.perf_counter()
    html = markdown.Markdown(extensions=ext).convert(docs[name])
    total += time.perf_counter() - start
    hashes[name] = hashlib.sha256(html.encode()).hexdigest()
print(json.dumps({"ms": total * 1000, "hashes": hashes}))
"""


def build_corpus(repo: Path) -> dict:
    docs = {}
    for folder in sorted({p.parent for p in (repo / "tests").rglob("*.txt")}):
        text = "\n\n".join(p.read_text(errors="replace") for p in sorted(folder.glob("*.txt")))
        if len(text) > 2000:
            docs[str(folder.relative_to(repo))] = text
    return docs


def main():
    repo, commit_b = Path(sys.argv[1]).resolve(), sys.argv[2]
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    tmp = Path(tempfile.mkdtemp(prefix="hca-indep-"))
    arms = {"A": tmp / "a", "B": tmp / "b"}
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(arms["A"]), "3.11.1"], check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(arms["B"]), commit_b], check=True)
    try:
        docs = build_corpus(arms["A"])
        corpus_file = tmp / "corpus.json"
        corpus_file.write_text(json.dumps(docs))
        warm = arms["A"] / "bench" / "warmup.md"
        warm = warm if warm.exists() else Path(__file__).resolve().parent / "bench" / "warmup.md"
        print(f"third corpus: {len(docs)} documents, {sum(len(t) for t in docs.values()) / 1024:.0f} KB")
        rng = random.Random(20261015)
        scores, raw = [], []
        hashes = {}
        for r in range(rounds):
            order = ["A", "B"]
            rng.shuffle(order)
            ms = {}
            for arm in order:
                out = subprocess.run([PY, "-c", WORKER, str(arms[arm]), str(corpus_file), json.dumps(EXTENSIONS), str(warm)],
                                     capture_output=True, text=True, check=True)
                data = json.loads(out.stdout)
                ms[arm] = data["ms"]
                hashes.setdefault(arm, data["hashes"])
            scores.append(stats.log_ratio(ms["A"], ms["B"], "min"))
            raw.append(ms)
        same = hashes["A"] == hashes["B"]
        test = stats.block_test(scores, 0.01)
        change = stats.metric_change_pct(test["estimate"], "min")
        bound = stats.metric_change_pct(test["lower"], "min") if math.isfinite(test["lower"]) else float("nan")
        result = {"documents": len(docs), "rounds": rounds, "output_identical": same,
                  "change_pct": change, "bound_pct": bound, "p_value": test["p_value"],
                  "median_ms_A": sorted(m["A"] for m in raw)[rounds // 2],
                  "median_ms_B": sorted(m["B"] for m in raw)[rounds // 2], "commit_b": commit_b}
        print(json.dumps(result, indent=1))
        (Path.home() / "hca-showcase" / "INDEPENDENT-CHECK.json").write_text(json.dumps(result, indent=1) + "\n")
    finally:
        for path in arms.values():
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(path)], check=False)


if __name__ == "__main__":
    main()
