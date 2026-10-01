# Retrieval comparison — 2026-10-01

Current Codex log support increases exact source-line recall from 4/11 to 10/11 on this synthetic corpus. Bounded normalized reads reduce the work and payload for partial session reads. Full-session responses remain identical.

## Revisions and environment

- Baseline: `5780bba6b8c35708981303b0e0381e9adcc2a910`.
- Candidate: `250ea0466ac6bf4a35d4485c8db42531c40bc15b`.
Both working trees were clean. The following documentation commit does not alter the measured implementation.
- Python 3.9.6; SQLite 3.51.0; macOS-26.5.1-arm64-arm-64bit.
- Corpus: 10,000-message Claude session and Codex format fixtures; SHA-256 `65e8b1f9ee440d9d98df62f2e7e2315f743948cfc654badc44fb775ca4b10c6c`.

Nine warm-index samples per read, with warm-up excluded. Times include normalized reads and JSON serialization. One separate tracemalloc sample measures Python allocation peak. Values below are rounded; [raw JSON](retrieval-2026-10-01.json) preserves all values and query-level judgments.

## Read comparison

| Operation | Baseline p50 / p95 (ms) | Candidate p50 / p95 (ms) | Python peak, baseline → candidate (MB) | JSON bytes, baseline → candidate | Messages, baseline → candidate |
|---|---:|---:|---:|---:|---:|
| Full session | 60.51 / 63.53 | 54.84 / 60.99 | 84.92 → 84.72 | 11,528,106 → 11,528,106 | 10,000 → 10,000 |
| Head 100 | 32.24 / 34.88 | 9.59 / 10.46 | 50.03 → 0.84 | 115,188 → 115,188 | 100 → 100 |
| Tail 100 | 34.09 / 37.22 | 10.22 / 15.23 | 50.03 → 0.85 | 115,607 → 115,607 | 100 → 100 |
| Web initial read | 65.34 / 66.80 | 15.52 / 18.58 | 84.91 → 1.54 | 11,528,106 → 209,593 | 10,000 → 181 |

The baseline Web path retrieves the entire session before rendering a window. The candidate reads 181 messages around the hit and loads adjacent pages on demand. Head/tail bodies and the anchored page match slices from the full-session oracle.

## Retrieval evaluation

| Case group | Baseline exact hits | Candidate exact hits |
|---|---:|---:|
| Long-session anchor and legacy English/CJK/AND queries | 4/4 | 4/4 |
| Completed user, assistant, plan, command output | 0/4 | 4/4 |
| Plaintext inter-agent message | 0/1 | 1/1 |
| Compressed archived Codex log | 0/1 | 1/1 |
| Answer beyond the existing 20k body cap | 0/1 | 0/1 |
| All gold queries | 4/11 (36.4%) | 10/11 (90.9%) |

Every answered query returns exactly its gold source line, with no extra hits. Precision is 1.0 for answered queries; zero-hit cases have precision 0 in the JSON. The clipped-tail case is an expected miss, making the indexing limit visible rather than implying complete coverage.

Full indexing: baseline 1053.2 ms, candidate 1033.0 ms (one run each). A representative warm FTS query has p50 12.44 → 13.75 ms. The change does not claim faster search ranking.

## Reproduction and acceptance

```bash
git worktree add --detach /tmp/mnemo-baseline 5780bba6b8c35708981303b0e0381e9adcc2a910
git worktree add --detach /tmp/mnemo-candidate 250ea0466ac6bf4a35d4485c8db42531c40bc15b
python3 /tmp/mnemo-candidate/scripts/benchmark-retrieval.py \
  --baseline /tmp/mnemo-baseline --candidate /tmp/mnemo-candidate \
  --messages 10000 --repeats 9 --check --output /tmp/retrieval.json
```

Install the optional `zstd` executable on the benchmark machine. CI runs the same correctness gates with 1,000 messages and five repetitions, uploads its JSON, and does not gate noisy timing values. Gates require all ten in-cap gold lines, no false hits, the expected clipped-tail miss, matching read slices and a bounded initial page. Unit and two-hop mesh tests cover stale/foreign cursors, compressed transitions, old-writer repair, unavailable decoders, route forwarding and failed-device coverage. Linux Playwright covers incremental page loading and the partial-history label.

## Limits

These are deterministic synthetic format and read tests, not real-history retrieval quality or end-to-end Agent answer evaluation. Baseline runs first; local concurrent load and cache effects can affect timing. Nine samples give a coarse p95. The measurements exclude process startup, SSH/network, index synchronization during reads, browser rendering, SQLite/native allocations and total process RSS. Raw pages still parse the whole source and are not claimed as a read-work optimization. Search coverage reports failed/skipped devices and index limits; it does not make clipped tails searchable.
