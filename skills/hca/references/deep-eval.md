# Deep evaluation (after every run, before any verdict)

A headline number answers one question. Read everything below before writing a verdict.

1. **Agreement.** Re-score from the raw per-item outputs. The result must match the in-run
   numbers exactly.
2. **Headline vs the matched control**, not vs the incumbent. A cohort chosen for the incumbent's
   weakness improves by chance in any retrained arm (regression to the mean).
3. **Noise margin** from the same-recipe replicate. Remember that one replicate is one draw, so
   the margin is itself uncertain. Cohort margins are wider than the headline margin.
4. **Slices and cohorts.** Look at every declared slice. Look for a narrow real effect hidden by
   cancellation in the headline, and for a gain paid for by a loss elsewhere.
5. **Mechanism metrics.** Did the thing the idea should move actually move, on the items it
   targets? A treatment that touches 5% of items can't show in the headline (dilution), so measure
   it where it acts.
6. **Failure examples.** Open the worst regressions and the best improvements as raw outputs
   (images, traces, diffs). A human or a capped agent (≤ ~7 heavy items each, with a checkable
   transcription field) labels them.
7. **Validity.** Count invalid and crashed items per arm, and say how they were scored.
8. **Selection history.** List every variant, seed and checkpoint looked at before this one.
   Adaptive selection inflates the winner, and only fresh data deflates it.
9. **Interpretation rules:**
   - "No evidence" is not "no effect". A null kills a claim only if its interval excludes the
     useful effect.
   - One current verdict per question. Visibly strike through any superseded headline.
10. **Decision**, split three ways:
    - engineering: ship it, keep it, or drop it;
    - science: what we now believe, and how strongly;
    - allocation: what to run next, or close the route.

    Then one next question.
