"""Differential fuzz: does the optimised Python-Markdown produce exactly the same output as 3.11.1?

Random documents are assembled from fragments covering every syntax the benchmark's extensions
handle (and the edge cases the optimisations touch: brackets, backticks, fences, `]:`, `>`, `#`,
rules, tables, admonitions, footnotes, abbreviations, smarty quotes, raw HTML, entities, escapes,
unicode). Each document is rendered by both versions in separate worker processes; the HTML plus toc,
toc_tokens, Meta and references must be identical.

  python diff_fuzz.py <repo> <commit> [docs] [seed]
"""

import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable  # run this script with the Python that has the project installed
EXTENSIONS = ["markdown.extensions.extra", "markdown.extensions.toc", "markdown.extensions.admonition",
              "markdown.extensions.sane_lists", "markdown.extensions.smarty", "markdown.extensions.meta",
              "markdown.extensions.wikilinks"]

WORKER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import markdown
assert markdown.__file__.startswith(sys.argv[1]), markdown.__file__
ext = json.loads(sys.argv[2])
out = []
for text in json.load(open(sys.argv[3])):
    md = markdown.Markdown(extensions=ext)
    try:
        html = md.convert(text)
        out.append(json.dumps({"html": html, "toc": getattr(md, "toc", None), "tokens": getattr(md, "toc_tokens", None),
                               "meta": getattr(md, "Meta", None), "refs": getattr(md, "references", None)},
                              sort_keys=True, default=str))
    except Exception as exc:
        out.append("EXC " + type(exc).__name__ + ": " + str(exc))
print(json.dumps(out))
"""

INLINE = ["*em*", "**strong**", "_u_", "__uu__", "***both***", "`code`", "``co`de``", "` `", "```", "[link](http://x.y)",
          "[ref][r1]", "[r1]", "![img](a.png \"t\")", "<http://auto.link>", "<b>raw</b>", "&amp;", "&copy;", "\\*not em\\*",
          "\\[", "\\]", "[[WikiLink]]", "\"quoted\"", "'single'", "--", "---", "...", "<<", ">>", "x^2", "a_b_c",
          "[unclosed", "]stray", "[a [nested] b](u)", "[x]:", "`unclosed", "**unclosed", "<!-- c -->", "é ü 中文 🎉",
          "*[ABBR]", "ABBR", "[^1]", "&#42;", "<span markdown=\"1\">*s*</span>", "line  ", "{: .cls }", "~~~", "#hash",
          "> quote", "1986\\. A", "\t", "  ", "[](empty)", "[t](<a b> \"x\")", "*a **b** c*", "_*mixed*_"]

BLOCKS = ["# H1", "## H2 ##", "###### H6", "Setext\n======", "Setext2\n------", "> quoted\n> more", "> nested\n>> deeper",
          "* a\n* b\n    * c", "1. one\n2. two", "3. three\n7. seven", "- [ ] task", "---", "***", "_ _ _", "- - -",
          "    indented code", "```python\ncode()\n```", "~~~\nfenced\n~~~", "```\nunclosed fence", "| a | b |\n|---|:-:|\n| 1 | 2 |",
          "a | b\n--|--\n1 | 2", "Term\n:   Definition", "!!! note \"Title\"\n    body", "!!! warning\n\n    para",
          "[r1]: http://ref.one \"Title\"", "[R2]: <http://two>", "  [r3]: http://three", "[^1]: Footnote text.",
          "*[ABBR]: Abbreviation", "<div>\nraw html block\n</div>", "<div markdown=\"1\">\n*md inside*\n</div>",
          "[TOC]", "Title: meta\nAuthor: x", "Para with  \nbreak", "    ", "", "\\# not a header", "#5 not a header?",
          ">not quote?", "a]: b", "-- not rule", "* * *", "1) paren list", "<p>p</p>", "    - code list", "```\n```",
          "``` {.lang #id}\nx\n```", "~~~~\n~~~\n~~~~", "#", ">", "]:", "[x]: ", "| only | pipes |"]


def make_doc(rng: random.Random) -> str:
    parts = []
    if rng.random() < 0.1:
        parts.append("Title: T\nTags: a, b\n")
    for _ in range(rng.randint(1, 12)):
        if rng.random() < 0.5:
            parts.append(rng.choice(BLOCKS))
        else:
            parts.append(" ".join(rng.choice(INLINE) for _ in range(rng.randint(1, 10))))
        if rng.random() < 0.3:
            parts[-1] = parts[-1] + " " + " ".join(rng.choice(INLINE) for _ in range(rng.randint(1, 4)))
    sep = rng.choice(["\n\n", "\n", "\n\n\n"])
    return sep.join(parts)


def render(tree: Path, docs_file: Path) -> list:
    out = subprocess.run(["nice", "-n", "19", PY, "-c", WORKER, str(tree), json.dumps(EXTENSIONS), str(docs_file)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def main():
    repo, commit = Path(sys.argv[1]).resolve(), sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 20261015
    rng = random.Random(seed)
    docs = [make_doc(rng) for _ in range(n)]
    tmp = Path(tempfile.mkdtemp(prefix="hca-fuzz-"))
    trees = {"old": tmp / "old", "new": tmp / "new"}
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(trees["old"]), "3.11.1"], check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(trees["new"]), commit], check=True)
    try:
        docs_file = tmp / "docs.json"
        docs_file.write_text(json.dumps(docs))
        old, new = render(trees["old"], docs_file), render(trees["new"], docs_file)
        diffs = [i for i in range(n) if old[i] != new[i]]
        exc = sum(o.startswith("EXC") for o in old)
        print(json.dumps({"docs": n, "seed": seed, "differences": len(diffs), "old_exceptions": exc}))
        for i in diffs[:5]:
            print("--- doc", i, repr(docs[i][:300]))
            print("old:", old[i][:400])
            print("new:", new[i][:400])
    finally:
        for path in trees.values():
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(path)], check=False)


if __name__ == "__main__":
    main()
