"""Draw the README's animated terminal: real HCA output from the Python-Markdown showcase.

Every output line is copied from what the CLI printed (ledger/report.txt, the attempt verdicts and
CLIMB-LOG.md); only the `-m` messages are shortened. Lines appear one after another and the cycle
loops. Plain CSS keyframes inside the SVG, which GitHub renders in READMEs.

  python3 docs/assets/make_terminal_svg.py
"""

from html import escape
from pathlib import Path

INK, DIM, MUTED, PROMPT = "#e8e6df", "#a9a79e", "#6f6d66", "#7fb2f0"
GOOD, BAD, NOTE = "#5cc28a", "#e66767", "#d9a54a"
BG, BAR = "#151514", "#262624"

# (delay seconds, kind, text). kind: cmd | out | keep | revert | pass | note | blank
SCRIPT = [
    (0.0, "note", "# real output from the Python-Markdown showcase (commit messages shortened)"),
    (1.0, "cmd", "hca quick -- python bench/hca_bench.py"),
    (2.6, "out", "Noise: ±0.6% (robust SD of A/A block log ratios, 20 blocks)."),
    (3.1, "out", "Detectable at 80% power: 0.7% at 10 blocks (~65 s per try), 0.5% at 20 blocks (~130 s)."),
    (3.6, "out", "Your useful effect 5%: OK (10 blocks are enough)."),
    (5.0, "cmd", 'hca try -m "skip inline patterns whose required character is absent"'),
    (6.8, "keep", "attempt 0004  KEEP  render_ms -28.6% (better; cut of at least 27.1%, p = 0.000977, 10 blocks, 80.5 s)"),
    (8.4, "cmd", 'hca try -m "dispatch inline patterns by trigger character"'),
    (10.2, "revert", "attempt 0009  REVERT  render_ms -1.7% (p = 0.00859 > 0.005, 20 blocks after extending, 112.7 s)"),
    (10.7, "out", "  a single run had read this one as -17.9%"),
    (12.2, "cmd", "hca confirm"),
    (14.0, "pass", "CONFIRM 4: PASS - estimate render_ms -53.8% (better) vs the original, cut of at least 53.0%"),
]
CYCLE = 20.0
W, LINE, TOP, LEFT = 960, 26, 58, 22


def main():
    height = TOP + LINE * len(SCRIPT) + 18
    style = ["text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13.5px;opacity:0}"]
    body = []
    for i, (delay, kind, text) in enumerate(SCRIPT):
        on = 100 * delay / CYCLE
        style.append(f"@keyframes k{i}{{0%,{on:.2f}%{{opacity:0}}{on + 1.5:.2f}%,94%{{opacity:1}}98%,100%{{opacity:0}}}}"
                     f".l{i}{{animation:k{i} {CYCLE}s linear infinite}}")
        y = TOP + LINE * i
        if kind == "cmd":
            body.append(f'<text class="l{i}" x="{LEFT}" y="{y}"><tspan fill="{PROMPT}">$ </tspan>'
                        f'<tspan fill="{INK}">{escape(text)}</tspan></text>')
            continue
        color = {"out": DIM, "note": MUTED, "keep": GOOD, "revert": BAD, "pass": GOOD}[kind]
        weight = ' font-weight="600"' if kind in ("keep", "revert", "pass") else ""
        indent = (len(text) - len(text.lstrip(" "))) * 8          # SVG collapses leading spaces
        body.append(f'<text class="l{i}" x="{LEFT + indent}" y="{y}" fill="{color}"{weight}>{escape(text.strip())}</text>')
    svg = ['<?xml version="1.0" encoding="UTF-8"?>', f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="img" '
           'aria-label="Terminal: hca quick measures the noise, hca try keeps a -28.6% change and reverts one a single run '
           'had read as -17.9%, and hca confirm passes at -53.8% on held-out documents.">',
           f'<style>{"".join(style)}</style>',
           f'<rect width="{W}" height="{height}" rx="12" fill="{BG}"/>',
           f'<rect width="{W}" height="34" rx="12" fill="{BAR}"/><rect y="22" width="{W}" height="12" fill="{BAR}"/>',
           '<circle cx="22" cy="17" r="6" fill="#e5534b"/><circle cx="42" cy="17" r="6" fill="#d9a54a"/>'
           '<circle cx="62" cy="17" r="6" fill="#5cc28a"/>',
           f'<text x="{W / 2}" y="22" text-anchor="middle" fill="{MUTED}" style="opacity:1;font-size:12px">'
           'hca · python-markdown</text>',
           *body, "</svg>"]
    out = Path(__file__).parent / "demo-terminal.svg"
    out.write_text("\n".join(svg) + "\n", encoding="utf-8")
    print("wrote", out.name)


if __name__ == "__main__":
    main()
