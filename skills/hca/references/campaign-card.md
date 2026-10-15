# Campaign card template (Tier 2: expensive runs)

One card per run. Write it before any number exists. Mark it `PREPARED, NOT LAUNCHED` until the
launch. Save it as `.hca/runs/<id>/CARD.md`, or in the project's experiments folder.

```markdown
# <id>: <the question as one sentence> (<hardware>; <date>; PREPARED, NOT LAUNCHED)

> Amendments: see §11. If a later section disagrees with an earlier one, the later one wins.

## 0. Question
Which decision does the result feed? What exactly is measured: metric, split, baseline,
instrument?

## 1. Premise check
(a) What produced the thing being changed (model, data, final training stage)? Is the
    evaluation data new to it?
(b) Baselines on the same split and runtime: the zero-effort version and the best public one.
(c) Can this intervention move the scored quantity at all? Where is the headroom?
(d) Step 0: the metric before any change, re-measured in this runtime.
Prior attempts: list each one and say what it actually tested. "Never tested the idea" is
`untested`, not `negative`.

## 2. Design
- Arms: the idea; the idea with a key component ablated; a MATCHED CONTROL with the same compute,
  steps and data volume but not the idea (this separates "more training" from the idea).
- Equal steps and matched data order across arms. Say how it is verified.
- Hyperparameters, each with "why this value".
- Seeds: at least one same-recipe seed replicate gives the noise margin.

## 3. Why this is decision-grade (and where it is not)

## 4. PREREGISTERED DECISION (frozen before any number; name the code that computes it)
- Verdicts: GO / NOT GO (inconclusive) / NEGATIVE / INVALID, each condition numbered.
- Statistic: unit of pairing, bootstrap or rank test, the baseline arm (the matched control,
  never the incumbent alone).
- Power: the detectable effect per cohort, from the replicate margin. Cohorts that are too small
  are marked UNDETECTABLE. Say plainly: "NOT GO must not be read as 'the idea fails'".
- How invalid or failed items are scored.
- Readouts that never change the verdict.

## 5. Mechanism metrics
What the idea should move directly, measured on the items it targets. These matter because
dilution hides real effects in the headline.

## 6. Gates (each tested in the simulation matrix)
| Gate | Where | Kill rule (hard stop unless marked) |

## 7. Budget
Expected and HIGH (x1.5) cost per arm, the provider's real rate (read from its UI, not assumed),
and a cap that survives one lost VM or run. Say plainly what fits under the cap.

## 8. Verification done before launch
Unit tests, simulation-matrix scenarios, and REAL-LIBRARY CALL-SITE TESTS (simulations that fake
a third-party call hide launch-killing bugs). Give counts and log paths.

## 9. Risks: R1..Rn, each with a mitigation and an interpretation rule

## 10. Status log (dated one-liners)

## 11. Amendments (pre-result only)
| # | date | change | was | reason | verification |
```

Keep/kill trigger: more than 2 review rounds, or 24 h with no efficacy number, forces an explicit
keep-or-kill decision.
