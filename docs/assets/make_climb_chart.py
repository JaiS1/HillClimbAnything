"""Draw the README climb charts from the showcase ledgers (light and dark SVGs).

Data: per-try verdicts and estimates from showcases/<name>/ledger/attempts/*/verdict.json; the
confirm results are the numbers `hca confirm` printed (ledger/report.txt); the single-run reading
of Python-Markdown try 9 is the first block's raw totals in that attempt's runs.jsonl.

  python3 docs/assets/make_climb_chart.py

Writes climb-{light,dark}.svg (Python-Markdown, the README hero) and climb-yaml-{light,dark}.svg (js-yaml).
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#8a8984", "grid": "#e6e5e1",
              "series": "#2a78d6", "naive": "#eb6834"},
    "dark": {"surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#8f8e86", "grid": "#33332f",
             "series": "#3987e5", "naive": "#d95926"},
}

W, H = 960, 440
L, R, T, B = 72, 150, 92, 64          # plot margins

SHOWCASES = {
    "python-markdown": {
        "file": "climb-{theme}.svg",
        "confirms": {3: -18.9, 7: -45.9, 13: -49.8, 17: -53.8},   # after try n: printed `hca confirm` estimate
        "naive": {9: -17.9},                                     # first-block raw reading (see the showcase README)
        "naive_text": {9: ("try 9: one run said −17.9%,", "20 blocks said −1.7% → reverted")},
        "label_dy": {7: -14},                                    # confirm labels placed above where the line runs below
        "y": (30.0, 100.0), "grid": (100, 80, 60, 40), "xticks": (1, 5, 10, 15, 18),
        "title": "Python-Markdown render time, one try at a time",
        "subtitle": "% of 3.11.1 · lower is better · line: kept changes on the 21 dev docs · diamonds: confirmed on held-out docs",
        "svg_title": "Python-Markdown render time across an HCA climb",
        "desc": "Render time as a percentage of version 3.11.1 over 18 tries. 14 kept changes step it down to "
                "about 39 percent on the 21 dev documents; four confirms on held-out documents passed at -18.9, -45.9, -49.8 and -53.8 percent. "
                "Try 9 read -17.9 percent on a single run but -1.7 percent over 20 blocks, and was reverted.",
        "final_lines": ("confirmed on", "held-out docs"),
        "keep_label": "kept (dev)",
    },
    "js-yaml": {
        "file": "climb-yaml-{theme}.svg",
        "confirms": {12: -15.3},
        "naive": {}, "naive_text": {}, "label_dy": {},
        "y": (70.0, 100.0), "grid": (100, 90, 80, 70), "xticks": (1, 4, 8, 12),
        "title": "js-yaml load() time, one try at a time",
        "subtitle": "% of 5.5.0 · lower is better · line: kept changes on the 491 train docs · diamond: confirmed on held-out docs",
        "svg_title": "js-yaml load() time across an HCA climb",
        "desc": "load() time as a percentage of js-yaml 5.5.0 over 12 tries. Three kept changes step it down to about 90 percent "
                "on the train documents; the one confirm on held-out documents passed at -15.3 percent. Nine tries were reverted or invalid.",
        "final_lines": ("confirmed on", "held-out docs"),
        "keep_label": "kept (train)",
    },
}


def load(name):
    tries = []
    for folder in sorted((ROOT / "showcases" / name / "ledger" / "attempts").glob("0*")):
        v = json.loads((folder / "verdict.json").read_text())
        tries.append((int(folder.name), v["verdict"], v.get("estimate")))
    return tries


def draw(name, theme):
    s = SHOWCASES[name]
    c = THEMES[theme]
    y0, y1 = s["y"]
    tries = load(name)
    count = len(tries)

    def x(n):
        return L + (W - L - R) * n / count

    def y(pct):
        return T + (H - T - B) * (y1 - pct) / (y1 - y0)

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
           f'<title id="t">{s["svg_title"]}</title>',
           f'<desc id="d">{s["desc"]}</desc>',
           f'<rect width="{W}" height="{H}" rx="12" fill="{c["surface"]}"/>',
           f'<text x="{L}" y="40" font-size="20" font-weight="650" fill="{c["ink"]}">{s["title"]}</text>',
           f'<text x="{L}" y="64" font-size="14" fill="{c["ink2"]}">{s["subtitle"]}</text>']
    for pct in s["grid"]:
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(pct):.1f}" y2="{y(pct):.1f}" stroke="{c["grid"]}" stroke-width="1"/>')
        out.append(f'<text x="{L - 10}" y="{y(pct) + 4:.1f}" font-size="12" text-anchor="end" fill="{c["ink2"]}">{pct}%</text>')
    for n in s["xticks"]:
        out.append(f'<text x="{x(n):.1f}" y="{H - B + 22}" font-size="12" text-anchor="middle" fill="{c["ink2"]}">{n}</text>')
    out.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 14}" font-size="12" text-anchor="middle" fill="{c["ink2"]}">try</text>')
    d = " ".join(("M" if i == 0 else "L") + f"{x(n):.1f},{y(p):.1f}" for i, (n, p) in enumerate(path))
    out.append(f'<path d="{d}" fill="none" stroke="{c["series"]}" stroke-width="2" stroke-linejoin="round"/>')
    for n, p in reverted:
        out.append(f'<circle cx="{x(n):.1f}" cy="{y(p):.1f}" r="4.5" fill="{c["surface"]}" stroke="{c["muted"]}" stroke-width="2"/>')
    for n, p in kept:
        out.append(f'<circle cx="{x(n):.1f}" cy="{y(p):.1f}" r="4.5" fill="{c["series"]}" stroke="{c["surface"]}" stroke-width="2"/>')
    final_y = None
    for n, est in s["confirms"].items():
        cx, cy = x(n), y(100 + est)
        out.append(f'<rect x="{cx - 6:.1f}" y="{cy - 6:.1f}" width="12" height="12" transform="rotate(45 {cx:.1f} {cy:.1f})" '
                   f'fill="{c["surface"]}" stroke="{c["series"]}" stroke-width="2"/>')
        if n == max(s["confirms"]):
            final_y = cy
            continue
        label = f"{est:+.1f}%".replace("-", "−")
        out.append(f'<text x="{cx:.1f}" y="{cy + s["label_dy"].get(n, 26):.1f}" font-size="12.5" font-weight="600" text-anchor="middle" fill="{c["ink"]}">{label}</text>')
    for n, est in s["naive"].items():
        base = [p for m, p in reverted if m == n][0]
        cx, cy = x(n), y(base * (1 + est / 100.0))
        line1, line2 = s["naive_text"][n]
        out.append(f'<line x1="{cx:.1f}" y1="{y(base) + 6:.1f}" x2="{cx:.1f}" y2="{cy - 6:.1f}" stroke="{c["naive"]}" stroke-width="1.5"/>')
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.5" fill="{c["naive"]}" stroke="{c["surface"]}" stroke-width="2"/>')
        out.append(f'<text x="{cx + 12:.1f}" y="{cy + 4:.1f}" font-size="12.5" fill="{c["ink"]}">{line1}</text>')
        out.append(f'<text x="{cx + 12:.1f}" y="{cy + 21:.1f}" font-size="12.5" fill="{c["ink2"]}">{line2}</text>')
    final = f"{max(s['confirms'].items())[1]:+.1f}%".replace("-", "−")
    lx, ly = x(count), final_y
    out.append(f'<text x="{lx + 14:.1f}" y="{ly - 6:.1f}" font-size="22" font-weight="700" fill="{c["ink"]}">{final}</text>')
    out.append(f'<text x="{lx + 14:.1f}" y="{ly + 14:.1f}" font-size="12" fill="{c["ink2"]}">{s["final_lines"][0]}</text>')
    out.append(f'<text x="{lx + 14:.1f}" y="{ly + 30:.1f}" font-size="12" fill="{c["ink2"]}">{s["final_lines"][1]}</text>')
    kx, ky = W - R + 14, T + 8
    key = [(s["keep_label"], f'<circle cx="{kx + 5}" cy="{ky}" r="4.5" fill="{c["series"]}"/>'),
           ("reverted", f'<circle cx="{kx + 5}" cy="{ky + 22}" r="4.5" fill="{c["surface"]}" stroke="{c["muted"]}" stroke-width="2"/>'),
           ("confirmed", f'<rect x="{kx}" y="{ky + 38}" width="10" height="10" transform="rotate(45 {kx + 5} {ky + 43})" fill="{c["surface"]}" stroke="{c["series"]}" stroke-width="2"/>')]
    if s["naive"]:
        key.append(("single run", f'<circle cx="{kx + 5}" cy="{ky + 66}" r="4.5" fill="{c["naive"]}"/>'))
    for i, (label, mark) in enumerate(key):
        out.append(mark)
        out.append(f'<text x="{kx + 18}" y="{ky + 4 + 22 * i}" font-size="12" fill="{c["ink2"]}">{label}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    for name, s in SHOWCASES.items():
        for theme in THEMES:
            fname = s["file"].format(theme=theme)
            (Path(__file__).parent / fname).write_text(draw(name, theme), encoding="utf-8")
            print("wrote", fname)
