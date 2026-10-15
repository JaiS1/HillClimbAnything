"""Draw the README hero chart from the Python-Markdown showcase ledger (light and dark SVGs).

Data: per-try verdicts and estimates from showcases/python-markdown/ledger/attempts/*/verdict.json;
the four confirm results are the numbers `hca confirm` printed (CLIMB-LOG.md and ledger/report.txt);
the single-run reading of try 9 is the first block's raw totals in that attempt's runs.jsonl.

  python3 docs/assets/make_climb_chart.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "showcases" / "python-markdown" / "ledger" / "attempts"
CONFIRMS = {3: -18.9, 7: -45.9, 13: -49.8, 17: -53.8}      # after try n: printed `hca confirm` estimate
NAIVE = {9: -17.9}                                       # first-block raw reading (see the showcase README)
LABEL_DY = {7: -14}                                      # confirm labels placed above where the line runs below

THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#8a8984", "grid": "#e6e5e1",
              "series": "#2a78d6", "naive": "#eb6834"},
    "dark": {"surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#8f8e86", "grid": "#33332f",
             "series": "#3987e5", "naive": "#d95926"},
}

W, H = 960, 440
L, R, T, B = 72, 150, 92, 64          # plot margins
Y0, Y1 = 30.0, 100.0                  # % of 3.11.1 render time


def load():
    tries = []
    for folder in sorted(LEDGER.glob("0*")):
        v = json.loads((folder / "verdict.json").read_text())
        tries.append((int(folder.name), v["verdict"], v["estimate"]))
    return tries


def x(n, count):
    return L + (W - L - R) * n / count


def y(pct):
    return T + (H - T - B) * (Y1 - pct) / (Y1 - Y0)


def draw(theme):
    c = THEMES[theme]
    tries = load()
    count = len(tries)
    level, path, kept, reverted = 100.0, [(0, 100.0)], [], []
    for n, verdict, est in tries:
        if verdict == "keep":
            path.append((n, level))
            level *= 1 + est / 100.0
            path.append((n, level))
            kept.append((n, level))
        else:
            reverted.append((n, level))
    path.append((count, level))
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'role="img" aria-labelledby="t d" font-family="ui-sans-serif, -apple-system, Segoe UI, Helvetica, Arial, sans-serif">',
           '<title id="t">Python-Markdown render time across an HCA climb</title>',
           '<desc id="d">Render time as a percentage of version 3.11.1 over 18 tries. 14 kept changes step it down to '
           'about 46 percent; four confirms on held-out documents passed at -18.9, -45.9, -49.8 and -53.8 percent. '
           'Try 9 read -17.9 percent on a single run but -1.7 percent over 20 blocks, and was reverted.</desc>',
           f'<rect width="{W}" height="{H}" rx="12" fill="{c["surface"]}"/>',
           f'<text x="{L}" y="40" font-size="20" font-weight="650" fill="{c["ink"]}">Python-Markdown render time, one try at a time</text>',
           f'<text x="{L}" y="64" font-size="14" fill="{c["ink2"]}">% of 3.11.1 · lower is better · line: kept changes on the 21 dev docs · diamonds: confirmed on held-out docs</text>']
    for pct in (100, 80, 60, 40):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(pct):.1f}" y2="{y(pct):.1f}" stroke="{c["grid"]}" stroke-width="1"/>')
        out.append(f'<text x="{L - 10}" y="{y(pct) + 4:.1f}" font-size="12" text-anchor="end" fill="{c["ink2"]}">{pct}%</text>')
    for n in (1, 5, 10, 15, 18):
        out.append(f'<text x="{x(n, count):.1f}" y="{H - B + 22}" font-size="12" text-anchor="middle" fill="{c["ink2"]}">{n}</text>')
    out.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 14}" font-size="12" text-anchor="middle" fill="{c["ink2"]}">try</text>')
    d = " ".join(("M" if i == 0 else "L") + f"{x(n, count):.1f},{y(p):.1f}" for i, (n, p) in enumerate(path))
    out.append(f'<path d="{d}" fill="none" stroke="{c["series"]}" stroke-width="2" stroke-linejoin="round"/>')
    for n, p in reverted:
        out.append(f'<circle cx="{x(n, count):.1f}" cy="{y(p):.1f}" r="4.5" fill="{c["surface"]}" stroke="{c["muted"]}" stroke-width="2"/>')
    for n, p in kept:
        out.append(f'<circle cx="{x(n, count):.1f}" cy="{y(p):.1f}" r="4.5" fill="{c["series"]}" stroke="{c["surface"]}" stroke-width="2"/>')
    levels = dict(kept)
    for n, est in CONFIRMS.items():
        cx, cy = x(n, count), y(100 + est)
        out.append(f'<rect x="{cx - 6:.1f}" y="{cy - 6:.1f}" width="12" height="12" transform="rotate(45 {cx:.1f} {cy:.1f})" '
                   f'fill="{c["surface"]}" stroke="{c["series"]}" stroke-width="2"/>')
        if n == max(CONFIRMS):
            final_y = cy
            continue
        label = f"{est:+.1f}%".replace("-", "−")
        out.append(f'<text x="{cx:.1f}" y="{cy + LABEL_DY.get(n, 26):.1f}" font-size="12.5" font-weight="600" text-anchor="middle" fill="{c["ink"]}">{label}</text>')
    for n, est in NAIVE.items():
        base = [p for m, p in reverted if m == n][0]
        cx, cy = x(n, count), y(base * (1 + est / 100.0))
        out.append(f'<line x1="{cx:.1f}" y1="{y(base) + 6:.1f}" x2="{cx:.1f}" y2="{cy - 6:.1f}" stroke="{c["naive"]}" stroke-width="1.5"/>')
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.5" fill="{c["naive"]}" stroke="{c["surface"]}" stroke-width="2"/>')
        out.append(f'<text x="{cx + 12:.1f}" y="{cy + 4:.1f}" font-size="12.5" fill="{c["ink"]}">try 9: one run said −17.9%,</text>')
        out.append(f'<text x="{cx + 12:.1f}" y="{cy + 21:.1f}" font-size="12.5" fill="{c["ink2"]}">20 blocks said −1.7% → reverted</text>')
    lx, ly = x(count, count), final_y
    out.append(f'<text x="{lx + 14:.1f}" y="{ly - 6:.1f}" font-size="22" font-weight="700" fill="{c["ink"]}">−53.8%</text>')
    out.append(f'<text x="{lx + 14:.1f}" y="{ly + 14:.1f}" font-size="12" fill="{c["ink2"]}">confirmed on</text>')
    out.append(f'<text x="{lx + 14:.1f}" y="{ly + 30:.1f}" font-size="12" fill="{c["ink2"]}">held-out docs</text>')
    kx, ky = W - R + 14, T + 8
    key = [("kept (dev)", f'<circle cx="{kx + 5}" cy="{ky}" r="4.5" fill="{c["series"]}"/>'),
           ("reverted", f'<circle cx="{kx + 5}" cy="{ky + 22}" r="4.5" fill="{c["surface"]}" stroke="{c["muted"]}" stroke-width="2"/>'),
           ("confirmed", f'<rect x="{kx}" y="{ky + 38}" width="10" height="10" transform="rotate(45 {kx + 5} {ky + 43})" fill="{c["surface"]}" stroke="{c["series"]}" stroke-width="2"/>'),
           ("single run", f'<circle cx="{kx + 5}" cy="{ky + 66}" r="4.5" fill="{c["naive"]}"/>')]
    for i, (name, mark) in enumerate(key):
        out.append(mark)
        out.append(f'<text x="{kx + 18}" y="{ky + 4 + 22 * i}" font-size="12" fill="{c["ink2"]}">{name}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    for theme in THEMES:
        (Path(__file__).parent / f"climb-{theme}.svg").write_text(draw(theme), encoding="utf-8")
        print("wrote", f"climb-{theme}.svg")
