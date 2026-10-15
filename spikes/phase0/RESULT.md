# Phase 0 result: is a noise-aware keep rule usable on a real, noisy home machine?

Date: 2026-10-14/15. Machine: WSL2, 16 cores, shared with a gaming desktop. Every run was nice'd,
sequential and seeded (20261014). Raw data is in `results/`. Reproduce it with `README.md`, plus
`reanalyze.py` and `reanalyze2.py`, which only read recorded timings.

## Question

Can a keep rule reject do-nothing changes, detect real 3–5% speedups, and do both in seconds per
try?

## What ran

| Target | What it measures | Arms and conditions | Tries |
|---|---|---|---|
| web (`items`) | TTFB+body of 12 server routes of a local stdlib HTTP app | Each block runs arm A then B, or B then A, at random. Conditions: A/A, and B made genuinely ~2.5% or ~4.5% faster | 12 per condition |
| cli (`repeats`) | wall time of a `compileall` workload | same | 20 per condition |

Each try measured 15 blocks. The achieved effects were web +2.42% / +4.49% and cli +2.53% / +4.59%.

## Findings

1. **Raw best-score selection is a coin flip.** Keeping B whenever its mean is better kept 50% of
   the do-nothing changes on cli and 17–25% on web. This is how most autoresearch-style loops
   decide.
2. **Treating items as independent units is anti-conservative for timing metrics.** On 1,000
   simulated A/A tries from recorded blocks, the route-as-unit bootstrap-t rejected at
   **6–12% when the nominal rate was 2.5%**. All routes in a block share that block's noise (host
   contention, the server process), so they are not independent.
3. **The v0.2 rule (route units + alpha/50) was safe only because it was very strict.** It had 0/96
   false keeps, but low power: cli at 15 blocks caught 65% of 5% wins. At 5 blocks the 99.9%
   bootstrap-t interval degenerates to ±1e12, so nothing can ever be kept.
4. **A sign-flip randomisation test on block scores is exactly calibrated and more powerful.** We
   used Wilcoxon signed-rank with an exact null, a Hodges–Lehmann estimate and a Walsh-average
   bound. The test is valid because arm order is randomised inside each block: under "no
   difference" the A/B labels are exchangeable, whatever the noise looks like.

   | | A/A false keeps (nominal 1%) | 5% win kept at k=10 / 15 / 20 |
   |---|---|---|
   | cli | 0.9% | 80% / 96% / 99% |
   | web (block score = median over routes) | 0.1% | 55% / 76% / 90% |

   It needs **k ≥ 10 blocks**. With k=5 the smallest possible p-value is 1/32.
5. **Changes at the keep floor stay coin flips by design.** A ~2.5% change, against a floor of half
   the 5% useful effect, was kept about 30–55% of the time. That is the intended behaviour: such a
   change is right at the edge of "not worth keeping".
6. **There was little drift or order effect.** The baseline drifted +0.17% (web) and −0.43% (cli)
   per 10 tries. The order effect was −0.29 ± 0.75% (web) and +0.33 ± 0.23% (cli). Web has heavy
   tails: 23 of 540 blocks were more than 4 robust SD from the median.

## Decision (design amendment A1, applied to `docs/DESIGN-hca.md` §3)

- **The default test for any measured (noisy) metric is a sign-flip / signed-rank test on
  randomised blocks.** Items are aggregated inside each block by a robust score, the median of the
  item log ratios. Per-item reads stay descriptive (slices). Generalisation across items is
  checked by `confirm` on held-out items.
- **The item bootstrap is kept only for deterministic per-item scores**, such as accuracy on an
  eval set, where there is no shared measurement noise.
- **Per-try alpha is 0.01 one-sided** (it replaces alpha/50). The minimum is 10 blocks; an
  inconclusive try may extend once to 20 blocks, with alpha split 0.005 + 0.005 across the two
  looks. The cumulative false keeps that remain are what `confirm` exists to catch.

## Limits

- Two local targets, one machine, and one evening.
- TTFB is not browser LCP; LCP noise will be higher.
- The effect sizes were hand-made.
- The simulated tries resample recorded blocks across the whole evening, which mixes slow drift
  into each try.
