"""The referee's arithmetic for noisy metrics: an exact signed-rank test on randomised blocks.

A block measures the baseline and the candidate back to back in random order. Under "no
difference" the two labels inside a block are exchangeable, so flipping the sign of a block's
score is equally likely either way. The exact null of the Wilcoxon signed-rank statistic over
all sign flips is therefore valid whatever the noise looks like (heavy tails, outlier blocks,
skew). Validity comes from the randomised order, so measurement code must randomise it.

Block scores are direction-adjusted log ratios: positive means the candidate is better.
Phase 0 (spikes/phase0/RESULT.md) measured this test at 0.1-0.9% false keeps for a nominal 1%.
Standard library only.
"""

from __future__ import annotations

import math
import statistics

from .core import HcaError

DEFAULT_ALPHA = 0.01        # one-sided, per try
MIN_BLOCKS = 10             # below this the exact test cannot reach small alphas
EXTENDED_BLOCKS = 20        # an inconclusive try may extend once to this many blocks


def log_ratio(baseline: float, candidate: float, direction: str) -> float:
    """Direction-adjusted log ratio: positive when the candidate is better."""
    if direction not in ("min", "max"):
        raise HcaError("direction must be min or max")
    if not (_positive(baseline) and _positive(candidate)):
        raise HcaError(f"log-ratio scores need positive finite values, got {baseline!r} and {candidate!r}")
    ratio = math.log(baseline / candidate)
    return ratio if direction == "min" else -ratio


def item_block_score(baseline: dict, candidate: dict, direction: str) -> float:
    """Median over items of the per-item log ratio within one block (robust to one bad item)."""
    if set(baseline) != set(candidate) or not baseline:
        raise HcaError("a block needs the same nonempty item ids in both arms")
    return statistics.median(log_ratio(baseline[key], candidate[key], direction) for key in sorted(baseline))


def to_percent(log_value: float) -> float:
    """A direction-adjusted log ratio as a % improvement (exp(x) - 1)."""
    return 100.0 * math.expm1(log_value)


def parse_effect(text) -> float:
    """'5%' -> log(1.05); a bare number is taken as a log ratio already."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        value = float(text)
    elif isinstance(text, str) and text.strip().endswith("%"):
        value = math.log1p(float(text.strip()[:-1]) / 100.0)
    else:
        raise HcaError(f"useful_effect must look like '5%' or a number, got {text!r}")
    if not math.isfinite(value) or value <= 0:
        raise HcaError("useful_effect must be positive")
    return value


def signed_rank_p(scores) -> float:
    """Exact one-sided p-value for 'scores are shifted above zero' (Wilcoxon, Pratt zeros, midranks)."""
    scores = _finite_list(scores)
    nonzero = [s for s in scores if s != 0]
    if not nonzero:
        return 1.0
    ranks = _midranks([abs(s) for s in scores])          # Pratt: zeros keep their ranks
    doubled = [round(2 * r) for r, s in zip(ranks, scores) if s != 0]   # midranks are halves
    observed = sum(d for d, s in zip(doubled, nonzero) if s > 0)
    counts = _null_counts(doubled)
    total = 2 ** len(doubled)
    return sum(counts[observed:]) / total


def hodges_lehmann(scores) -> float:
    """Median of Walsh averages: the shift estimate that matches the signed-rank test."""
    return statistics.median(_walsh(_finite_list(scores)))


def walsh_lower_bound(scores, alpha: float) -> float:
    """One-sided (1 - alpha) lower confidence bound for the shift; -inf if n is too small.

    Exact for untied scores; with ties it is the usual approximation (the test itself is exact).
    """
    scores = _finite_list(scores)
    walsh = sorted(_walsh(scores))
    counts = _null_counts([2 * r for r in range(1, len(scores) + 1)])
    total = 2 ** len(scores)
    cumulative, c = 0, 0
    for value in range(0, len(counts), 2):            # untied doubled sums are even
        if (cumulative + counts[value]) / total > alpha:
            break
        cumulative += counts[value]
        c += 1
    return walsh[c - 1] if c else float("-inf")


def smallest_p(n: int) -> float:
    """The smallest one-sided p-value n blocks can produce (all positive): 2**-n."""
    return 2.0 ** -n


def block_test(scores, alpha: float = DEFAULT_ALPHA) -> dict:
    """Signed-rank test, Hodges-Lehmann estimate and bound for one set of block scores."""
    scores = _finite_list(scores)
    if len(scores) < 2:
        raise HcaError("a block test needs at least two blocks")
    p_value = signed_rank_p(scores)
    estimate = hodges_lehmann(scores)
    return {"blocks": len(scores), "alpha": alpha, "p_value": p_value, "reject": p_value <= alpha,
            "estimate": estimate, "estimate_pct": to_percent(estimate),
            "lower": walsh_lower_bound(scores, alpha),
            "can_reject": smallest_p(len(scores)) <= alpha}


def keep_decision(scores, useful: float, alpha: float = DEFAULT_ALPHA, look: int = 1, looks: int = 1) -> dict:
    """Keep a candidate only if the test rejects AND the estimate is at least half the useful effect.

    With an optional extension (looks=2) each look is tested at alpha/2, so the per-try false-keep
    rate stays at or below alpha. Verdicts: keep | revert | extend | insufficient.
    """
    if looks not in (1, 2) or look not in range(1, looks + 1):
        raise HcaError("look must be 1..looks and looks must be 1 or 2")
    level = alpha / looks
    result = block_test(scores, level)
    floor = 0.5 * useful
    result.update({"useful": useful, "keep_floor": floor, "look": look, "looks": looks, "reasons": []})
    if not result["can_reject"]:
        result["verdict"] = "insufficient"
        result["reasons"].append(f"{len(scores)} blocks cannot reach p <= {level:g} (need >= {math.ceil(math.log2(1 / level))})")
        return result
    if not result["reject"]:
        result["reasons"].append(f"p = {result['p_value']:.3g} > {level:g}")
    if result["estimate"] < floor:
        result["reasons"].append(f"estimate {result['estimate_pct']:+.2f}% is below the keep floor "
                                 f"{to_percent(floor):+.2f}% (half the useful effect)")
    if not result["reasons"]:
        result["verdict"] = "keep"
    elif look < looks and result["estimate"] > 0:
        result["verdict"] = "extend"       # promising but unproven: measure more blocks once
    else:
        result["verdict"] = "revert"
    return result


def noise_report(aa_scores, useful: float, alpha: float = DEFAULT_ALPHA / 2, power: float = 0.8) -> dict:
    """From A/A block scores: the noise floor and the smallest shift detectable at 10 and 20 blocks.

    The default alpha is the per-look level of the two-look keep rule (keep_decision with looks=2).

    Uses a robust SD (1.4826 * MAD) and the normal approximation to the signed-rank test's power
    with its 0.955 relative efficiency. It is a planning number, not a guarantee; heavy tails
    make the rank test do better than this approximation suggests, outlier-free normal noise
    roughly as stated.
    """
    scores = _finite_list(aa_scores)
    if len(scores) < 5:
        raise HcaError("noise needs at least five A/A blocks")
    centre = statistics.median(scores)
    mad = statistics.median(abs(s - centre) for s in scores)
    sd = 1.4826 * mad
    if sd == 0:
        sd = statistics.pstdev(scores)
    z_alpha = statistics.NormalDist().inv_cdf(1 - alpha)
    z_power = statistics.NormalDist().inv_cdf(power)
    detectable = {}
    for blocks in (MIN_BLOCKS, EXTENDED_BLOCKS):
        shift = (z_alpha + z_power) * sd / math.sqrt(0.955 * blocks)
        detectable[blocks] = shift
    verdict = ("ok" if detectable[MIN_BLOCKS] <= useful else
               "needs-extension" if detectable[EXTENDED_BLOCKS] <= useful else "too-noisy")
    return {"blocks": len(scores), "aa_centre": centre, "aa_centre_pct": to_percent(centre),
            "noise_sd": sd, "noise_sd_pct": to_percent(sd) if sd else 0.0,
            "detectable": detectable, "detectable_pct": {k: to_percent(v) for k, v in detectable.items()},
            "useful": useful, "useful_pct": to_percent(useful), "verdict": verdict,
            "aa_offset_flag": abs(centre) > 0.5 * useful}


def _positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _finite_list(scores) -> list:
    scores = list(scores)
    for value in scores:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise HcaError(f"block scores must be finite numbers, got {value!r}")
    return scores


def _walsh(scores):
    n = len(scores)
    return [(scores[i] + scores[j]) / 2 for i in range(n) for j in range(i, n)]


def _midranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _null_counts(weights):
    """counts[s] = number of sign patterns whose positive-weight sum is s (integer weights)."""
    counts = [1]
    for weight in weights:
        nxt = counts + [0] * weight
        for total, count in enumerate(counts):
            if count:
                nxt[total + weight] += count
        counts = nxt
    return counts
