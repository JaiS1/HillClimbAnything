"""HCA objective for Python-Markdown: time to render a corpus of real documents.

Items are documents. The metric for an item is the median of RUNS renders (ms). Each run is a fresh
worker process that first renders a separate warm-up document (so imports, regex compilation and
code paths are warm), then renders every corpus document exactly ONCE. So a cache keyed on document
content can never be hit within a measurement: an optimisation has to make rendering itself faster.

The worker runs the code under test; this parent never imports markdown. The parent times every
render itself (a request/response round trip over a pipe), checks the full output (HTML plus toc,
toc_tokens, Meta and references) against recorded hashes, and writes the result. Workers get no
HCA_* environment variables and a fixed PYTHONHASHSEED. A document whose output differs is scored
candidate_invalid.

Not measured: interpreter start-up, `import markdown`, and the warm-up render.

  python bench/hca_bench.py            # measure (dev corpus, or held-out when HCA_ITEMS=heldout)
  python bench/hca_bench.py --record   # write expected output hashes for the corpus in use
  python bench/hca_bench.py --child    # internal: the render worker
"""

import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WARMUP = ROOT / "bench" / "warmup.md"
EXTENSIONS = ["markdown.extensions.extra", "markdown.extensions.toc", "markdown.extensions.admonition",
              "markdown.extensions.sane_lists", "markdown.extensions.smarty", "markdown.extensions.meta",
              "markdown.extensions.wikilinks"]
RUNS = 5


def child():
    """Render worker: one JSON request per line in, one JSON reply per line out."""
    sys.path.insert(0, str(ROOT))          # this checkout's markdown, not an editable install elsewhere
    import markdown
    if Path(markdown.__file__).resolve().parents[1] != ROOT:
        print(json.dumps({"error": f"imported markdown from {markdown.__file__}, expected {ROOT}"}), flush=True)
        return
    print(json.dumps({"ready": markdown.__file__}), flush=True)
    for line in sys.stdin:
        md = markdown.Markdown(extensions=EXTENSIONS)
        html = md.convert(json.loads(line)["text"])
        extra = {"toc": getattr(md, "toc", None), "toc_tokens": getattr(md, "toc_tokens", None),
                 "meta": getattr(md, "Meta", None), "references": getattr(md, "references", None)}
        print(json.dumps({"html": html, "extra": extra}, sort_keys=True, default=str), flush=True)


class Worker:
    def __init__(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("HCA_")}
        env["PYTHONHASHSEED"] = "0"
        self.proc = subprocess.Popen([sys.executable, __file__, "--child"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, env=env)
        hello = json.loads(self.proc.stdout.readline() or '{"error": "worker died"}')
        if "error" in hello:
            sys.exit(hello["error"])
        self.origin = hello["ready"]

    def render(self, text):
        """(output digest, html bytes, ms): the parent's own clock around one request/response."""
        message = json.dumps({"text": text}) + "\n"
        start = time.perf_counter()
        self.proc.stdin.write(message)
        self.proc.stdin.flush()
        reply = self.proc.stdout.readline()
        elapsed = (time.perf_counter() - start) * 1000.0
        if not reply:
            raise RuntimeError("render worker died")
        data = json.loads(reply)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(), len(data["html"]), elapsed

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


def corpus():
    if os.environ.get("HCA_ITEMS") == "heldout":
        folder = Path(os.environ["HCA_HELDOUT_DIR"])
    else:
        folder = ROOT / "bench" / "corpus"
    return folder, sorted(folder.glob("*.md"))


def one_run(docs, texts, order_seed):
    """One fresh worker: warm up on a separate document, then each doc exactly once, in a shuffled order."""
    worker = Worker()
    try:
        worker.render(WARMUP.read_text())
        order = list(docs)
        random.Random(order_seed).shuffle(order)
        results = {doc.name: worker.render(texts[doc.name]) for doc in order}
    finally:
        worker.close()
    return worker.origin, results


def main():
    if "--child" in sys.argv:
        return child()
    folder, docs = corpus()
    texts = {doc.name: doc.read_text() for doc in docs}
    expected_path = folder / "expected.json"
    if "--record" in sys.argv:
        _, results = one_run(docs, texts, 0)
        expected_path.write_text(json.dumps({name: r[0] for name, r in results.items()}, indent=1, sort_keys=True) + "\n")
        print(f"recorded {len(results)} documents to {expected_path}", file=sys.stderr)
        return
    expected = json.loads(expected_path.read_text())
    seed = random.SystemRandom().randrange(2 ** 32)
    runs = [one_run(docs, texts, seed + i) for i in range(RUNS)]
    items = []
    for doc in docs:
        per_run = [results[doc.name] for _, results in runs]
        ok = all(digest == expected.get(doc.name) for digest, _, _ in per_run)
        items.append({"id": doc.stem, "runs": [ms for _, _, ms in per_run], "status": "valid" if ok else "candidate_invalid",
                      "tags": {"kind": doc.stem.split("-")[0]}, "mechanism": {"html_kb": per_run[0][1] / 1024}})
    total = sum(statistics.median(i["runs"]) for i in items)
    print(f"rendered {len(items)} docs x {RUNS} fresh workers, median total {total:.1f} ms, markdown from {runs[0][0]}",
          file=sys.stderr)
    result = json.dumps({"metric": "render_ms", "items": items})
    if os.environ.get("HCA_RESULT_FILE"):
        Path(os.environ["HCA_RESULT_FILE"]).write_text(result + "\n")
    else:
        print(result)


if __name__ == "__main__":
    main()
