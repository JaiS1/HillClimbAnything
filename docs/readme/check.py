"""Mechanical checks for docs/readme/README.<lang>.md against README.md.

Run after editing README.md or a translation: python3 docs/readme/check.py
It checks structure counts, that every relative link resolves, that the key numbers survive
(local thousands separators allowed), that there is no anchor nav row, and the source-commit line.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EN = (ROOT / "README.md").read_text(encoding="utf-8")
NUMBERS = ["53.8", "53.0", "63.5", "17.9", "1.7", "1,126", "110", "23,000", "17–55", "76–96", "3.11.1", "28.6",
           "15.3", "13.3", "25,384", "11,250",
           "0.6", "282", "3.11"]
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)|(?:src|srcset)=\"([^\"]+)\"")


def counts(text):
    return {"headings": len(re.findall(r"^#{1,3} ", text, re.M)), "fences": text.count("```") // 2,
            "details": text.count("<details>"), "table_rows": len(re.findall(r"^\|", text, re.M)),
            "mermaid": text.count("```mermaid")}


def main():
    en = counts(EN)
    bad = 0
    for path in sorted((ROOT / "docs" / "readme").glob("README.*.md")):
        text = path.read_text(encoding="utf-8")
        problems = []
        c = counts(text)
        for key, value in en.items():
            # headings: the translation adds none and removes none (nav row is not a heading)
            if c[key] != value:
                problems.append(f"{key} {c[key]} != {value}")
        for m in LINK.finditer(text):
            target = m.group(1) or m.group(2)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).resolve().exists():
                problems.append(f"broken link {target}")
        for number in NUMBERS:
            variants = {number, number.replace(",", "."), number.replace(",", " "), number.replace(",", " "),
                        number.replace(",", " "), number.replace(",", "")}
            if not any(v in text for v in variants):
                problems.append(f"missing number {number}")
        if any(line.count("](#") >= 3 for line in text.splitlines()):
            problems.append("nav row still present")
        if not re.search(r"[0-9a-f]{7}", text.splitlines()[0]):
            problems.append("first line lacks the source commit")
        bad += bool(problems)
        print(f"{path.name:22} {'OK' if not problems else '; '.join(problems)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
