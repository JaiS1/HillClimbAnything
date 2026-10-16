# Verdicts: yaml-load

Updated 2026-10-16T06:32:44-0700. One current verdict; superseded ones are struck through.

## Current verdict

Confirmed: load_ms -15.3% (better) vs the original (cut of at least 13.3%; confirm 1, 20 blocks).

Question: do edits to src/** move `load_ms` (min) by at least 5%?

## Superseded

- ~~No keeps yet.~~ (as of 2026-10-16T05:30:45-0700)
- ~~No confirmed result yet. Unconfirmed cumulative -2.7% over 1 keep(s): a sum of per-try estimates that selection inflates; run `hca confirm`.~~ (as of 2026-10-16T05:49:55-0700)
- ~~No confirmed result yet. Unconfirmed cumulative -7.5% over 2 keep(s): a sum of per-try estimates that selection inflates; run `hca confirm`.~~ (as of 2026-10-16T05:54:58-0700)
- ~~No confirmed result yet. Unconfirmed cumulative -10.4% over 3 keep(s): a sum of per-try estimates that selection inflates; run `hca confirm`.~~ (as of 2026-10-16T06:27:42-0700)

## Attempts

- 0001 revert -2.0% p=0.00534 - T1: skip checkPrintable slice+regex for plain scalars unless a non-printable-ASCII char was seen while scanning (saves a slice and regex exec per scalar); total_ms/slices checked in report
- 0002 revert - T2: record nodeTags (Map.set per mapping sequence element) only when the schema defines a merge tag, since only << merge reads it; removes a Map insertion per mapping-in-sequence. Effect on total_ms/slices to check in report
- 0003 invalid - T2b: record nodeTags (Map.set per mapping sequence element) only when the schema defines a merge tag (resolved lazily so odd-schema errors are unchanged), since only << merge reads it; removes a Map insertion per mapping-in-sequence
- 0004 revert +0.5% p=0.652 - T2b: record nodeTags (Map.set per mapping sequence element) only when the schema defines a merge tag (resolved lazily so odd-schema errors are unchanged), since only << merge reads it; removes a Map insertion per mapping-in-sequence
- 0005 revert +0.8% p=0.722 - T3: avoid allocating a ParserSnapshot per parseNode call and per block-mapping key (capture fields in locals, build the object only when a property is read or a restore is needed); fewer allocations/GC on the hottest parser paths
- 0006 keep -2.7% p=0.000241 - T4 bundle of three avoid-work changes, each ~1% alone (T1/T2/T3 reverted separately as sub-noise; local A/B of the bundle: ~-4%): (a) skip checkPrintable slice+regex for plain scalars with only printable-ASCII chars, (b) skip nodeTags Map.set unless schema has a merge tag, (c) no ParserSnapshot allocation per parseNode call / per block-mapping key (confirmed)
- 0007 keep -4.9% p=0.000977 - T5: parseNode no longer allocates a NodeProperties object per node: share one never-written empty object and allocate only when a '!' or '&' property is about to be read (the loop also now breaks on a first-char check before calling both property readers). Mechanism: fewer allocations + calls on every node. Local A/B ~-6% (confirmed)
- 0008 revert -0.7% p=0.0266 - T6: parseNode dispatches on the node's first character before calling the block-sequence, flow-collection, block-scalar, quoted-scalar and alias readers (each began with the same char test and returned false), so a plain scalar skips ~5 failed calls per node. Local A/B ~-3.3%
- 0009 revert -1.8% p=0.00319 - T7: cut calls per scalar node: parseNode dispatches on first char before the sequence/flow/block-scalar/quoted/alias readers (T6 idea, which measured -0.7% alone) plus readPlainScalar pushes its scalar event inline instead of via the 11-arg addScalarEvent. Local A/B ~-2..-5%
- 0010 revert +0.0% p=0.615 - T8: T7 (first-char dispatch in parseNode + inline plain scalar event; measured -1.8%, p=0.003, below floor) plus two more per-call/per-scalar cuts: ASCII char-code table for implicit scalar resolver dispatch (no charAt+Map.get per scalar), and load option key lists hoisted to module scope with an indexed pick loop (no Object.keys x2 per load call)
- 0011 revert -2.1% p=0.00142 - T9: loadDocuments fast path for the common no-options call: pass the shared default parser options and a bare {source} to constructFromEvents instead of merging {...defaults,...options}, Object.keys x2 and two pick() copies per call (identical values). Targets fixed per-call cost that dominates small documents (median item); local A/B median item ~-2..-4%, total ~-1%
- 0012 keep -3.2% p=0.000293 - T10: combine three individually sub-floor but real wins (T7 -1.8% p=.003, T9 -2.1% p=.001): (1) no-options load() fast path in loadDocuments, (2) parseNode first-char dispatch before sequence/flow/block-scalar/quoted/alias readers + inline plain scalar event, (3) readPlainScalar scans ordinary ASCII runs with a table lookup and a local cursor (total_ms should improve ~8% locally). Local A/B median item ~-5% (confirmed)
