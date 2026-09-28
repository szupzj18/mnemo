"""Generate a synthetic HOME with Claude Code / Codex / Pi session logs.

Usage: python3 scripts/make-demo-home.py /tmp/demo-home && HOME=/tmp/demo-home mnemo index
Used for docs screenshots and manual testing without touching real sessions.
"""
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone

HOME = os.path.abspath(sys.argv[1])
USER_HOME = "/home/alex"
MARKER = os.path.join(HOME, ".mnemo-demo")
# Only ever wipe a directory this script created itself.
if os.path.exists(HOME) and os.listdir(HOME) and not os.path.exists(MARKER):
    sys.exit("refusing to overwrite %s: not an empty dir or a previous demo home" % HOME)
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME)
open(MARKER, "w").close()


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# Each script step: (seconds_after_previous, kind, payload)
#   kind: user | say | think | call(name, args) | result(text)

def claude(project, start, steps):
    sid = str(uuid.uuid4())
    cwd = USER_HOME + "/" + project
    t = start
    rows = []
    for dt, kind, *p in steps:
        t += timedelta(seconds=dt)
        base = {"sessionId": sid, "cwd": cwd, "timestamp": iso(t)}
        if kind == "user":
            rows.append(dict(base, type="user", message={"role": "user", "content": p[0]}))
        elif kind == "say":
            rows.append(dict(base, type="assistant", message={"role": "assistant", "content": [{"type": "text", "text": p[0]}]}))
        elif kind == "think":
            rows.append(dict(base, type="assistant", message={"role": "assistant", "content": [{"type": "thinking", "thinking": p[0]}]}))
        elif kind == "call":
            rows.append(dict(base, type="assistant", message={"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": p[0], "input": p[1]}]}))
        elif kind == "result":
            rows.append(dict(base, type="user", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": p[0]}]}))
    d = os.path.join(HOME, ".claude", "projects", cwd.replace("/", "-"))
    write(os.path.join(d, sid + ".jsonl"), rows)


def codex(project, start, steps):
    sid = str(uuid.uuid4())
    cwd = USER_HOME + "/" + project
    t = start
    rows = [{"type": "session_meta", "timestamp": iso(t), "payload": {"id": sid, "cwd": cwd}}]
    for dt, kind, *p in steps:
        t += timedelta(seconds=dt)
        ts = iso(t)
        if kind == "user":
            pl = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": p[0]}]}
        elif kind == "say":
            pl = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": p[0]}]}
        elif kind == "think":
            pl = {"type": "reasoning", "summary": [{"type": "summary_text", "text": p[0]}]}
        elif kind == "call":
            pl = {"type": "function_call", "name": p[0], "arguments": json.dumps(p[1])}
        elif kind == "result":
            pl = {"type": "function_call_output", "output": p[0]}
        rows.append({"type": "response_item", "timestamp": ts, "payload": pl})
    d = os.path.join(HOME, ".codex", "sessions", start.strftime("%Y/%m/%d"))
    write(os.path.join(d, "rollout-%s-%s.jsonl" % (start.strftime("%Y-%m-%dT%H-%M-%S"), sid)), rows)


def pi(project, start, steps):
    sid = str(uuid.uuid4())
    cwd = USER_HOME + "/" + project
    t = start
    rows = [{"type": "session", "id": sid, "cwd": cwd, "timestamp": iso(t)}]
    for dt, kind, *p in steps:
        t += timedelta(seconds=dt)
        ts = iso(t)
        if kind == "user":
            m = {"role": "user", "content": [{"type": "text", "text": p[0]}]}
        elif kind == "say":
            m = {"role": "assistant", "content": [{"type": "text", "text": p[0]}]}
        elif kind == "think":
            m = {"role": "assistant", "content": [{"type": "thinking", "thinking": p[0]}]}
        elif kind == "call":
            m = {"role": "assistant", "content": [{"type": "toolCall", "name": p[0], "arguments": p[1]}]}
        elif kind == "result":
            m = {"role": "toolResult", "content": [{"type": "text", "text": p[0]}]}
        rows.append({"type": "message", "timestamp": ts, "message": m})
    d = os.path.join(HOME, ".pi", "agent", "sessions", "--" + cwd.strip("/").replace("/", "-") + "--")
    write(os.path.join(d, "%s_%s.jsonl" % (start.strftime("%Y-%m-%dT%H-%M-%S-000Z"), sid)), rows)


TZ = timezone(timedelta(hours=8))
D = lambda day, h, m: datetime(2026, 9, day, h, m, 0, tzinfo=TZ)

# 1. Claude: diagnosing flaky webhook deliveries -> exponential backoff with jitter
claude("relay", D(21, 10, 12), [
    (0, "user", "Webhook deliveries to slow receivers keep failing in staging. Find out why the retry loop gives up so fast."),
    (4, "think", "The retry loop lives in relay/delivery.py. Check how the delay is computed before touching the queue."),
    (2, "say", "I'll start with the retry loop in `relay/delivery.py`."),
    (1, "call", "Bash", {"command": "rg -n 'retry|backoff' relay/delivery.py"}),
    (3, "result", "42:    for attempt in range(MAX_RETRIES):\n47:        time.sleep(RETRY_DELAY)  # fixed 200ms\n51:    raise DeliveryError('gave up after %d retries' % MAX_RETRIES)"),
    (5, "say", "Retries use a **fixed 200ms delay**. Five attempts finish within one second, so any receiver that takes longer than that to recover is marked dead. A thundering herd also forms: every failed webhook retries at the same instant."),
    (140, "user", "Switch it to exponential backoff with jitter, capped at 30s."),
    (3, "call", "Edit", {"file_path": "relay/delivery.py", "old_string": "time.sleep(RETRY_DELAY)", "new_string": "time.sleep(backoff(attempt))"}),
    (1, "result", "The file relay/delivery.py has been updated."),
    (2, "call", "Edit", {"file_path": "relay/backoff.py", "new_string": "def backoff(attempt, base=0.5, cap=30.0):\n    return random.uniform(0, min(cap, base * 2 ** attempt))"}),
    (1, "result", "Created relay/backoff.py"),
    (2, "call", "Bash", {"command": "pytest tests/test_delivery.py -q"}),
    (4, "result", "........                                                  [100%]\n8 passed in 3.91s"),
    (3, "say", "Done. `backoff()` uses *full jitter*: a uniform delay between 0 and `min(30s, 0.5s * 2^attempt)`. The 8 delivery tests pass. The slow-receiver case now survives about 90s of downtime before the delivery is dead-lettered."),
])

# 2. Codex: same repo, later — adding a retry budget, mentions backoff
codex("relay", D(24, 15, 40), [
    (0, "user", "Add a per-endpoint retry budget so one broken receiver can't eat the whole worker pool. Keep the existing backoff."),
    (3, "think", "Token bucket per endpoint, refilled over time. Backoff stays as-is; the budget only decides whether a retry is allowed."),
    (2, "call", "exec_command", {"cmd": "rg -n 'def backoff' -A3 relay/"}),
    (2, "result", "relay/backoff.py:3:def backoff(attempt, base=0.5, cap=30.0):\nrelay/backoff.py-4-    return random.uniform(0, min(cap, base * 2 ** attempt))"),
    (6, "say", "Adding `RetryBudget` — a token bucket keyed by endpoint host, 10 retries/minute by default. When the budget is empty the delivery goes straight to the dead-letter queue instead of sleeping."),
    (2, "call", "apply_patch", {"input": "*** Add File: relay/budget.py\n+class RetryBudget: ..."}),
    (1, "result", "Success. Updated the following files:\nA relay/budget.py\nM relay/delivery.py"),
    (2, "call", "exec_command", {"cmd": "pytest -q"}),
    (6, "result", "27 passed in 6.02s"),
    (2, "say", "All 27 tests pass. The budget is configured through `RELAY_RETRY_BUDGET` (default `10/min`)."),
])

# 3. Pi: rust side project, borrow checker
pi("playground/ringbuf", D(19, 21, 5), [
    (0, "user", "Why does this fail to compile? `let first = &buf[0]; buf.push(4); println!(\"{}\", first);`"),
    (3, "think", "Immutable borrow `first` is still live when push takes &mut self."),
    (2, "call", "bash", {"command": "cargo build 2>&1 | head -12"}),
    (5, "result", "error[E0502]: cannot borrow `buf` as mutable because it is also borrowed as immutable\n --> src/main.rs:4:5\n  |\n3 |     let first = &buf[0];\n  |                  --- immutable borrow occurs here\n4 |     buf.push(4);\n  |     ^^^^^^^^^^^ mutable borrow occurs here"),
    (3, "say", "It's **E0502**. `first` borrows `buf` immutably, and `push` may reallocate the Vec, which would leave `first` dangling. Either copy the value first (`let first = buf[0];`) or print before pushing. Thanks to NLL, the borrow ends at its last use, so reordering the lines is enough."),
    (200, "user", "And for the ring buffer, should retries on a full buffer use backoff too?"),
    (4, "say", "For an in-process ring buffer, spinning with a short exponential backoff (for example `std::hint::spin_loop` then `thread::yield_now`) is fine. Save sleeping backoff with jitter for network retries."),
])

# 4. Claude: a separate project, dashboard work (noise + second source for cwd filter)
claude("docs-site", D(25, 9, 30), [
    (0, "user", "The search page flashes unstyled content on first load. Fix it."),
    (3, "call", "Bash", {"command": "rg -n 'stylesheet' src/layouts/Base.astro"}),
    (2, "result", "12:  <link rel=\"stylesheet\" href=\"/theme.css\" media=\"print\" onload=\"this.media='all'\">"),
    (4, "say", "The theme stylesheet is loaded async with the `media=print` trick, so the page paints once without it. Making it a normal blocking stylesheet fixes the flash; it's 3 KB, so the cost is negligible."),
    (1, "call", "Edit", {"file_path": "src/layouts/Base.astro", "old_string": "media=\"print\" onload=\"this.media='all'\"", "new_string": ""}),
    (1, "result", "The file src/layouts/Base.astro has been updated."),
    (2, "say", "Fixed; the flash is gone in a cold-cache reload."),
])

# 5. Codex: CI incident
codex("relay", D(26, 11, 5), [
    (0, "user", "CI is red on main: test_backoff_is_bounded is flaky. Investigate."),
    (3, "call", "exec_command", {"cmd": "pytest tests/test_backoff.py -q -p no:randomly --count 200"}),
    (9, "result", "1 failed, 199 passed\nFAILED tests/test_backoff.py::test_backoff_is_bounded - assert 30.000000000000004 <= 30.0"),
    (4, "say", "A floating-point edge case: `min(cap, base * 2 ** attempt)` is fine, but the test computes the bound independently and compares with `<=`. I'll assert with `pytest.approx` and clamp explicitly in `backoff()`."),
    (2, "call", "apply_patch", {"input": "*** Update File: relay/backoff.py\n-    return random.uniform(0, min(cap, base * 2 ** attempt))\n+    return min(cap, random.uniform(0, base * 2 ** attempt))"}),
    (1, "result", "Success. Updated the following files:\nM relay/backoff.py"),
    (2, "say", "Fixed and verified with 1000 runs: no failures."),
])

print("demo home:", HOME)
