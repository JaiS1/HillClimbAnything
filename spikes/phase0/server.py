#!/usr/bin/env python3
"""Phase 0 target 1: a stdlib ThreadingHTTPServer with 12 routes doing realistic page work.

Every route builds its data deterministically from a seeded RNG, then does JSON / HTML
templating / sort / filter work. `--scale` multiplies every route's data size (the
"knob"): variant A runs scale 1.0, variant B runs a slightly smaller scale so it does
less deterministic CPU work per request. Identical code, separate process per variant.
"""
import argparse
import html
import json
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCALE = 1.0
WORDS = [w for w in ("alpha beta gamma delta <tag> & quote\" apple orange river stone cloud "
                     "engine signal market vector kernel basket window garden harbor island "
                     "ledger matrix object packet render socket thread update widget yellow "
                     "zephyr o'neil a&b x<y").split()]


def n(base):
    return max(1, int(round(base * SCALE)))


def words(rng, k):
    return " ".join(rng.choice(WORDS) for _ in range(k))


def catalog():
    rng = random.Random(1)
    items = [{"id": i, "name": words(rng, 3), "price": round(rng.uniform(1, 500), 2),
              "tags": [rng.choice(WORDS) for _ in range(4)], "desc": words(rng, 12)}
             for i in range(n(520))]
    return "application/json", json.dumps({"items": items, "count": len(items)})


def listing():
    rng = random.Random(2)
    rows = []
    for i in range(n(950)):
        rows.append("<li class='row'><a href='/item/%d'>%s</a> <span>%s</span></li>"
                    % (i, html.escape(words(rng, 3)), html.escape(words(rng, 6))))
    return "text/html", "<ul>" + "\n".join(rows) + "</ul>"


def search():
    rng = random.Random(3)
    recs = [{"id": i, "name": words(rng, 2), "score": rng.random(), "cat": rng.randrange(20)}
            for i in range(n(2600))]
    hits = [r for r in recs if r["cat"] % 3 != 0 and "a" in r["name"]]
    hits.sort(key=lambda r: (-r["score"], r["name"]))
    return "application/json", json.dumps({"hits": hits[:40], "total": len(hits)})


def report():
    rng = random.Random(4)
    agg = {}
    for _ in range(n(5200)):
        key = "g%d" % rng.randrange(60)
        a = agg.setdefault(key, [0, 0.0, 0.0])
        v = rng.gauss(100, 15)
        a[0] += 1
        a[1] += v
        a[2] += v * v
    out = []
    for key in sorted(agg):
        c, s, ss = agg[key]
        mean = s / c
        out.append({"group": key, "n": c, "mean": round(mean, 3),
                    "sd": round(max(ss / c - mean * mean, 0) ** 0.5, 3)})
    return "application/json", json.dumps(out)


def table():
    rng = random.Random(5)
    out = ["<table>"]
    for i in range(n(520)):
        cells = "".join("<td>%s</td>" % html.escape(words(rng, 2)) for _ in range(6))
        out.append("<tr id='r%d'>%s</tr>" % (i, cells))
    out.append("</table>")
    return "text/html", "\n".join(out)


def tree():
    rng = random.Random(6)
    nodes = [{"id": 0, "children": []}]
    for i in range(1, n(2300)):
        parent = nodes[rng.randrange(len(nodes))]
        node = {"id": i, "label": words(rng, 2), "children": []}
        parent["children"].append(node)
        nodes.append(node)
    return "application/json", json.dumps(nodes[0], sort_keys=True)


def feed():
    rng = random.Random(7)
    posts = [{"ts": rng.randrange(10 ** 9), "user": "u%d" % rng.randrange(500), "text": words(rng, 15)}
             for _ in range(n(1300))]
    posts.sort(key=lambda p: -p["ts"])
    body = "".join("<article data-ts='%d'><b>%s</b><p>%s</p></article>"
                   % (p["ts"], html.escape(p["user"]), html.escape(p["text"])) for p in posts[:120])
    return "text/html", body


def widget():
    rng = random.Random(8)
    items = [{"k": i, "v": words(rng, 2), "w": rng.random()} for i in range(n(1400))]
    return "application/json", json.dumps(items)


def profile():
    rng = random.Random(9)
    sections = []
    for i in range(n(520)):
        sections.append("<section><h3>%s</h3><p>%s</p></section>"
                        % (html.escape(words(rng, 2)), html.escape(words(rng, 20))))
    return "text/html", "<div class='profile'>%s</div>" % "".join(sections)


def stats():
    rng = random.Random(10)
    vals = sorted(rng.gauss(0, 1) for _ in range(n(5300)))
    q = {str(p): vals[int(p / 100 * (len(vals) - 1))] for p in (1, 5, 25, 50, 75, 95, 99)}
    hist = [0] * 20
    for v in vals:
        hist[min(19, max(0, int((v + 4) * 2.5)))] += 1
    return "application/json", json.dumps({"q": q, "hist": hist, "mean": sum(vals) / len(vals)})


def text():
    rng = random.Random(11)
    counts = {}
    for _ in range(n(900)):
        for w in words(rng, 14).split():
            counts[w] = counts.get(w, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:30]
    return "text/html", "<ol>%s</ol>" % "".join("<li>%s: %d</li>" % (html.escape(w), c) for w, c in top)


def export():
    rng = random.Random(12)
    lines = []
    for i in range(n(3800)):
        lines.append(json.dumps({"i": i, "a": rng.random(), "b": words(rng, 3), "c": [i, i + 1]}))
    return "application/x-ndjson", "\n".join(lines)


ROUTES = {"/catalog": catalog, "/listing": listing, "/search": search, "/report": report,
          "/table": table, "/tree": tree, "/feed": feed, "/widget": widget, "/profile": profile,
          "/stats": stats, "/text": text, "/export": export}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        fn = ROUTES.get(self.path)
        if fn is None:
            self.send_error(404)
            return
        ctype, body = fn()
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main():
    global SCALE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--scale", type=float, default=1.0)
    args = ap.parse_args()
    SCALE = args.scale
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print("READY", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
