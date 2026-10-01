# Benchmarks

## Reproducible retrieval comparison

The synthetic benchmark compares a clean baseline checkout with the candidate on identical logs, in separate Python workers and SQLite indexes. It measures normalized full/head/tail/Web-first-page reads, and evaluates top-20 recall against explicit source-line answers. It never uses real session history or registered devices.

```bash
git worktree add --detach /tmp/mnemo-baseline 5780bba6b8c35708981303b0e0381e9adcc2a910
python3 scripts/benchmark-retrieval.py --baseline /tmp/mnemo-baseline \
  --messages 10000 --repeats 9 --output /tmp/retrieval.json
```

The default corpus contains a 10,000-message Claude session plus legacy, completed-item, inter-agent and compressed Codex logs. Eleven fixed queries have source-line ground truth; one deliberately places its answer beyond the 20k body cap. Use `zstd` on the benchmark machine to generate compressed fixtures. The raw JSON records the corpus SHA-256, Python/SQLite/platform versions, revisions, dirty flags, indexing time, per-query results and read metrics.

| Measurement | Method | Acceptance criterion |
|---|---|---|
| Format coverage | Correct `(path, lineno)` in top 20 for each gold query | All ten in-cap cases; clipped-tail case reported as a miss |
| Returned-hit precision | Exact gold line / returned hits for each query | No extra hits for these unique-answer queries |
| Read correctness | Compare selected messages against a full-session oracle | Head/tail/page equality; no message loss |
| Latency | Warm-up excluded; 9 warm-index runs, read plus JSON serialization | Report p50/p95; no fixed cross-machine timing gate |
| Memory | One separate `tracemalloc` run per operation | Report Python allocation peak; indexed selection must not load all bodies |
| Payload | UTF-8 JSON bytes | Web initial read bounded to 181 messages |
| Federation correctness | Hermetic two-hop local-process tests plus offline-device cases | Cursors/routes preserved; failed devices not reported as successful zero-hit searches |

Index build timing is a single run per variant; baseline runs first. Latency includes local read/serialization and excludes process startup, SSH/network, index sync and agent reasoning. Memory excludes SQLite/native allocations and total RSS. Raw pagination is not benchmarked as an optimization because it still parses the original file. This is a format/read regression suite, not an estimate of real-world answer accuracy.

Measured results are in [the retrieval report](benchmarks/retrieval-report.md). CI runs a smaller correctness smoke test; timing remains informational.

## Historical real-session measurements

Measured on 2026-09-24 against Mnemo v0.1.0 (then named *agentsearch*) on one developer's real session history. The machine was macOS with the system Python 3.9 and SQLite 3.51. These are single-machine numbers. Federated search adds one SSH round-trip per device (about 300–450 ms in practice to a remote devbox, with connections reused after the first query).

## Corpus

| | |
|---|---|
| Sources | Claude Code, Codex (incl. archived), Pi |
| Session files | 730 |
| Indexed messages | 149,678 |
| Raw log size | 3.6 GB |
| Index size | 784 MB (21.8% of raw) |

## Indexing

| Operation | Time |
|---|---|
| Full build, 730 files / 150k messages | 28.9 s (first run only) |
| Incremental sync, nothing changed | 0.07–0.13 s |
| MCP server start to ready, including sync | 63–101 ms (median 69 ms) |

## Query latency

End-to-end CLI times, including ~40 ms of Python start-up. Each figure is the median of 5 runs.

| Query | Time |
|---|---|
| English, one word | 106 ms |
| Chinese, two words | 58 ms |
| Mixed Chinese + English | 51 ms |
| English, two words | 46 ms |

Context lookup:

| Implementation | Largest session (44,549 lines) |
|---|---|
| Re-parse the JSONL on every call | 316 ms |
| Index rowid range (`file_ranges`) | **7 ms** |

Both implementations returned identical results on 13 of 13 sampled hits. The index-based version also keeps working after a session file is moved or archived.

## Token cost per call

Heuristic estimate: CJK ≈ 0.75 tok/char, ASCII ≈ 0.28 tok/char, ±20%.

| `limit` | English | Chinese | Mixed |
|---|---|---|---|
| 5 | ~790 | ~800 | ~930 |
| 10 | ~1,610 | ~1,610 | ~1,880 |
| 20 | ~3,180 | ~3,390 | ~3,650 |

One retrieval round (search with limit 10 plus 2 × context), sampled over 6 real queries, used a median of ~7,000 tokens (range 6,500–31,400). The upper end comes from context windows that land on a single huge tool output.

Fixed overhead: MCP tool schemas ~500 tok per session; skill description ~130 tok, and the full skill ~755 tok when it triggers.

## Compared with grep

A line-by-line substring scan over all 729 session files:

| Keyword | Matching lines | Raw output | Scan time | Mnemo top 20 |
|---|---|---|---|---|
| common English term | 16,548 | 1,541 MB | 4.35 s | ~3,200 tok / <100 ms |
| common Chinese term | 1,940 | 457 MB | 4.55 s | ~3,300 tok / <100 ms |
| common acronym | 37,745 | 2,313 MB | 4.30 s | ~3,400 tok / <100 ms |

Grep returns whole JSON lines, gigabytes of them, with no ranking and no notion of message role. An agent has to narrow the search down many times before the output fits in context.

## End-to-end experiment

**Question:** does giving an agent Mnemo make it cheaper to answer "what did we do back then?" questions?

**Setup.** Each task was run by two fresh subagents on the same model:

- **Mnemo group:** allowed only `search` and `context`.
- **Control group:** Mnemo disabled; allowed only `rg`/`grep`/`python` over the raw session directories.
- Both groups could use only session logs, not curated notes. From T2 on, sessions from the experiment day were excluded, after T1's control run found its answer in the experiment's own live transcript.
- Ground truth was verified before each launch.

| Task | Kind of lookup |
|---|---|
| T1 | Single-hop fact: an ID created in a session on a given day |
| T2 | Multi-hop across sessions: an approval ID from one day, plus three metrics from a report on the next day |
| T3 | Deeply buried fact: a commit hash and whether its target branch was a release branch |
| T4 | Cross-agent: a compiler error code and conclusion that exist only in a Pi session |

### Results

All 8 runs answered correctly.

| Task | Group | Tokens | Tool calls | Wall time (s) | Data read |
|---|---|---:|---:|---:|---:|
| T1 | Mnemo | 44,972 | 9 | 100 | 32.5 KB |
| T1 | grep | 50,237 | 15 | 128 | 42 KB |
| T2 | Mnemo | 130,246 | 20 | 549 | 130 KB |
| T2 | grep | 191,172 | 48 | 897 | 400 KB |
| T3 | Mnemo | 41,708 | 8 | 89 | 31.6 KB |
| T3 | grep | 54,594 | 20 | 232 | 40.6 KB |
| T4 | Mnemo | 42,377 | 7 | 72 | 28.7 KB |
| T4 | grep | 40,045 | 8 | 101 | 22.4 KB |
| **Total** | **Mnemo** | **259,303** | **44** | **810** | |
| **Total** | **grep** | **336,048** | **91** | **1,358** | |
| | | **−23%** | **−52%** | **−40%** | |

### Observations

- **The gain grows with the search space.** T2 required stitching together sessions from different days across hundreds of files. The grep agent needed 48 commands and read 400 KB of raw JSON, while the Mnemo agent needed 20 commands, used 32% fewer tokens and took 39% less wall time. On T3, wall time dropped by 62%.
- **T4 is the counter-example (+6% tokens).** Pi had only 86 session files, and their directory names encode the working directory, so grep went straight to the right file. Mnemo saves effort in *finding* the answer. When the target set is small and its path is guessable, grep is just as good.
- **There is a fixed floor of ~41–45k tokens per task,** spent on reasoning and on composing the answer, whatever the retrieval method.

### Caveats

n = 4 tasks, one model, one run per condition, and therefore no variance estimate. The tasks share a narrow set of topics, and file names may have given the grep group hints. Read these results as the direction of the effect, not its size.
