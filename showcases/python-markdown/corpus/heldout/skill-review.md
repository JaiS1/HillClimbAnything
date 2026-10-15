# Adversarial review (before every Tier 2 launch)

The reviewer should be a different model, or at least a fresh agent instance, with read-only
access to the card, the code diff and the spec. Its job is to find what is wrong, not to agree.

## Prompt skeleton

> You are an adversarial reviewer. Find what will make this run fail, mislead, or waste money.
> Check claims against the code, not just the card. Attack: the premise check; whether the control
> separates the idea from extra compute; equal steps and data order; power and the detectable
> effect per cohort; how invalid items are scored; budget arithmetic (HIGH estimate, one lost run);
> gates that fail open; anything simulated that would behave differently for real; and provenance
> (commit, shas, env).

## Output format (saved next to the card as `REVIEW-adversarial.md`)

1. **Verdict:** BLOCKER(s) or none, with the count of MUST and SHOULD-FIX findings.
2. **Verified OK:** claims checked against the code, each in one line.
3. **MUST / SHOULD-FIX S1..Sn:** evidence (file:line), why it matters, the concrete fix, and the
   cost of the fix.
4. **NIT / questions.** These never block.
5. **Not reviewed:** what the reviewer did not look at.

## After the review

- Write a dated amendment for each finding: adopted, partly adopted, or rebutted, each with its
  evidence. Rebut with data, not tone.
- **At most one re-review per card.** Review overhead scales with cost, not anxiety. Most findings
  in long review chains turn out to be hygiene.
- Launch. A review lowers the risk of launch failures but does not remove it.

## After results: the debate

Before writing a verdict, give a critic the full result and the decision tables. Answer each
point ADOPT / REJECT / PARTLY, verified against the data. Record it next to the result. A
provisional reading stays labelled PROVISIONAL until then.
