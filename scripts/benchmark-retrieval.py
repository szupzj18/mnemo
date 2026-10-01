#!/usr/bin/env python3
"""Compare two checkouts using synthetic logs only; no real HOME or network.

python3 scripts/benchmark-retrieval.py --baseline ../agentsearch --output /tmp/retrieval.json
"""
import argparse
import hashlib
import inspect
import json
import math
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc


def corpus(home, messages):
    gold = []

    def write(relative, records, cases=()):
        path = os.path.join(home, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        for query, line in cases:
            gold.append({"query": query, "path": relative, "lineno": line})
        return path

    def raw(text, role="assistant"):
        return {"type": "response_item", "timestamp": "2026-10-01T00:00:00Z", "payload": {
            "type": "message", "role": role, "content": [{"type": "output_text", "text": text}]}}

    long_path = ".claude/projects/-synthetic/long.jsonl"
    records = []
    for i in range(messages):
        text = "retry budget 退避 tracing synthetic row %d " % i + "bounded retrieval sample " * 40
        if i == messages // 2:
            text += " midpointneedle"
        records.append({"type": "user", "sessionId": "synthetic-long", "cwd": "/synthetic", "timestamp": "2026-10-01T00:00:00Z",
                        "message": {"role": "user", "content": text}})
    write(long_path, records, [("midpointneedle", messages // 2 + 1)])
    meta = {"type": "session_meta", "payload": {"id": "synthetic-codex", "cwd": "/synthetic"}}
    write(".codex/sessions/legacy.jsonl", [meta, raw("legacyneedle 兼容读取", "user"), raw("retry decision budgetneedle")],
          [("legacyneedle", 2), ("兼容读取", 2), ("decision budgetneedle", 3)])
    events = [meta]
    for item in (
        {"type": "UserMessage", "id": "u", "content": [{"type": "text", "text": "eventuserneedle"}]},
        {"type": "AgentMessage", "id": "a", "content": [{"type": "Text", "text": "eventassistantneedle"}]},
        {"type": "Plan", "id": "p", "text": "eventplanneedle"},
        {"type": "CommandExecution", "id": "c", "command": ["echo", "sample"], "aggregated_output": "eventoutputneedle"},
    ):
        events.append({"type": "event_msg", "timestamp": "2026-10-01T00:00:00Z", "payload": {
            "type": "item_completed", "turn_id": "turn1", "item": item}})
    write(".codex/sessions/events.jsonl", events, [("eventuserneedle", 2), ("eventassistantneedle", 3),
                                                 ("eventplanneedle", 4), ("eventoutputneedle", 5)])
    write(".codex/sessions/interagent.jsonl", [meta, {"type": "response_item", "timestamp": "2026-10-01T00:00:00Z", "payload": {
        "type": "agent_message", "content": [{"type": "input_text", "text": "agentmessageneedle"}]}}], [("agentmessageneedle", 2)])
    compressed = write(".codex/archived_sessions/compressed.jsonl", [meta, raw("compressedneedle")], [("compressedneedle", 2)])
    subprocess.run(["zstd", "-q", "-f", compressed, "-o", compressed + ".zst"], check=True)
    os.remove(compressed)
    # This is deliberately outside the indexed cap: both revisions should miss it.
    write(".codex/sessions/truncated.jsonl", [meta, raw("x" * 22000 + " tailonlyneedle")], [("tailonlyneedle", 2)])
    digest = hashlib.sha256()
    for root, dirs, names in os.walk(home):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            digest.update(os.path.relpath(path, home).encode())
            with open(path, "rb") as f:
                digest.update(f.read())
    return {"long_path": long_path, "gold": gold, "messages": messages, "sha256": digest.hexdigest()}


def worker(args):
    sys.path.insert(0, os.path.abspath(args.repo))
    os.environ["HOME"] = args.home
    for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        os.environ.pop(key, None)
    from mnemo.index import Index
    from mnemo.search import get_session, search
    index = Index(args.db)
    start = time.perf_counter()
    index.sync(home=args.home)
    indexing_ms = (time.perf_counter() - start) * 1000
    with open(args.manifest) as f:
        manifest = json.load(f)
    path = os.path.join(args.home, manifest["long_path"])
    paging = "limit" in inspect.signature(get_session).parameters
    full = get_session(index, path)
    checks = []
    for case in manifest["gold"]:
        hits = search(index, case["query"], limit=20)
        expected = (os.path.join(args.home, case["path"]), case["lineno"])
        correct = [(h["path"], h["lineno"]) == expected for h in hits]
        checks.append(dict(case, found=any(correct), precision_at_20=(sum(correct) / len(hits) if hits else 0), returned=len(hits)))
    center = manifest["messages"] // 2

    def read(name):
        if name == "full":
            return get_session(index, path)
        if paging:
            if name == "web_initial":
                return get_session(index, path, limit=181, anchor_line=center + 1)
            return get_session(index, path, **{name: 100})
        result = get_session(index, path)
        if name != "web_initial":
            result["messages"] = result["messages"][:100] if name == "head" else result["messages"][-100:]
        return result

    metrics = {}
    for name in ("full", "head", "tail", "web_initial"):
        read(name)  # warm-up, excluded
        times = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            value = read(name)
            encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
            times.append((time.perf_counter() - start) * 1000)
        tracemalloc.start()
        value = read(name)
        json.dumps(value, ensure_ascii=False).encode("utf-8")
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        metrics[name] = {"p50_ms": statistics.median(times), "p95_ms": sorted(times)[math.ceil(.95 * len(times)) - 1],
                         "python_peak_bytes": peak, "response_bytes": len(encoded), "returned_messages": len(value["messages"])}
        expected = full["messages"] if name == "full" or (name == "web_initial" and not paging) else (
            full["messages"][:100] if name == "head" else full["messages"][-100:] if name == "tail" else full["messages"][center - 90:center + 91])
        if value["messages"] != expected:
            raise AssertionError("retrieval differs from full-session oracle: " + name)
    search_times = []
    for _ in range(args.repeats):
        start = time.perf_counter()
        search(index, "retry budget", limit=20)
        search_times.append((time.perf_counter() - start) * 1000)
    result = {"revision": subprocess.check_output(["git", "-C", args.repo, "rev-parse", "HEAD"], universal_newlines=True).strip(),
              "working_tree_dirty": bool(subprocess.check_output(["git", "-C", args.repo, "status", "--porcelain"])),
              "supports_paging": paging, "indexing_ms": indexing_ms,
              "search_p50_ms": statistics.median(search_times), "retrieval": metrics, "evaluation": checks,
              "recall_at_20": sum(c["found"] for c in checks) / len(checks)}
    index.close()
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="unmodified checkout used as baseline")
    parser.add_argument("--candidate", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--messages", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=9)
    parser.add_argument("--check", action="store_true", help="gate synthetic correctness, never latency")
    parser.add_argument("--output", default="/tmp/mnemo-retrieval-benchmark.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    for flag in ("repo", "home", "db", "manifest"):
        parser.add_argument("--" + flag, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    if not args.baseline or not shutil.which("zstd") or args.messages < 400 or args.repeats < 5:
        parser.error("require --baseline, zstd, messages >= 400 and repeats >= 5")
    if subprocess.check_output(["git", "-C", args.baseline, "status", "--porcelain"]):
        parser.error("baseline checkout must be clean")
    with tempfile.TemporaryDirectory(prefix="mnemo-benchmark-") as root:
        home = os.path.join(root, "home")
        manifest = corpus(home, args.messages)
        manifest_path = os.path.join(root, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)
        result = {"python": platform.python_version(), "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
                  "repeats": args.repeats, "corpus": manifest, "variants": {}}
        for name, repo in (("baseline", args.baseline), ("candidate", args.candidate)):
            command = [sys.executable, os.path.abspath(__file__), "--worker", "--repo", os.path.abspath(repo),
                       "--home", home, "--manifest", manifest_path, "--db", os.path.join(root, name + ".db"),
                       "--repeats", str(args.repeats)]
            result["variants"][name] = json.loads(subprocess.check_output(command, universal_newlines=True))
    if args.check:
        candidate = result["variants"]["candidate"]
        for case in candidate["evaluation"]:
            expected = case["query"] != "tailonlyneedle"
            if case["found"] != expected or (expected and case["precision_at_20"] != 1):
                raise AssertionError("format-recall regression: " + case["query"])
        if not candidate["supports_paging"] or candidate["retrieval"]["web_initial"]["returned_messages"] > 181:
            raise AssertionError("initial session response must be paged")
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(args.output)


if __name__ == "__main__":
    main()
