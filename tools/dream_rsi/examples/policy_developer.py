"""Deterministic protocol fixture, NOT an LLM or learned research policy.

Proposes a small early-stopping code revision so the CPU example can exercise
the complete development/replay/promotion/redeployment mechanism without keys.
"""
import json
import sys

request = json.load(sys.stdin)
source = request["incumbent"]["source"]
marker = "# BEGIN deterministic developer fixture"
if marker in source:
    start = source.index(marker)
    end = source.index("# END deterministic developer fixture", start)
    source = source[:start] + source[end + len("# END deterministic developer fixture"):]

threshold = 1 if request["revision"] % 2 else 2
rule = f'''{marker}
# Deliberately scripted proposal: stop after {threshold} consecutive invalid attempts.
if len(nodes) >= {threshold} and all(n['status'] != 'valid' for n in nodes[-{threshold}:]):
    answer = None
# END deterministic developer fixture
'''
needle = "print(json.dumps({'action': answer}))"
if needle not in source:
    raise ValueError("fixture requires the supplied baseline policy format")
source = source.replace(needle, rule + needle)
print(json.dumps({"source": source, "summary":
    f"Scripted test proposal: stop after {threshold} consecutive invalid attempts. "
    "This exercises source evolution; it is not an LLM-generated discovery."}))
