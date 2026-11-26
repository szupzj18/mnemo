# Distilling sessions into knowledge cards

Experimental. Only when the knowledge base is on (`mnemo node --json` shows `"kb": true`, or `MNEMO_KB=on`).

Each step has an MCP tool and a CLI command; use the MCP tools when you have them. They write cards through mnemo,
which checks every field, so they work from any directory and inside an agent's sandbox. With the CLI, write the
card files yourself and run from the kb dir (`~/.mnemo/kb`, or `$MNEMO_KB_DIR`).

## Loop

| step | MCP tool | CLI |
|---|---|---|
| 1. sessions with undistilled messages, newest first | `list_undistilled_sessions` | `mnemo digest pending --json` |
| 2. one session's material | `get_digest(path)` | `mnemo digest show <path>` |
| 3. find a card that already covers a claim | `search_sessions(query, kinds="knowledge")`, `list_cards(id)` | `mnemo search <words> --kind knowledge --json` |
| 4. create, update or supersede a card | `save_card` | edit `<kb dir>/<id>.md` |
| 5. record the session as distilled | `mark_distilled(path, upto, cards)` | `mnemo digest done <path> --upto N --cards a,b` |
| 6. check every card parses | `list_cards` (no `errors`) | `mnemo kb ls` (no `!!`) |

The material (step 2) has `session`, then `turns` (each user message, the last tool calls for it, and the answer
that closed it), `cards` (cards citing this session) and `related` (live cards sharing its distinctive words).

Decide what in it is worth keeping (below). For each candidate claim, look for a card that already covers it:
`cards` and `related` first, then search with one or two distinctive words. Search ANDs every word, so a long
query finds nothing; search for `IPQoS`, not "ssh mac mini hangs".

- Same knowledge, still true: add the source, and move `verified` to that turn's `date` if it is later. Do not
  create a card. When the session adds a detail to the same claim, fold it into the body; when the detail would be
  looked up on its own (another port, another device), it is a new card. With `save_card`, pass the id and only
  what changes: sources are added to the card's own.
- The session shows a card is now wrong (an address changed, a decision was reversed) and its turn is later than
  the card's `verified`: write the new card and mark the old one superseded (`save_card(..., supersedes=<old id>)`,
  or `superseded_by: <new id>` in the old file). Never edit an old card's content into a different claim. An older
  session that disagrees changes nothing: it is history.
- Nothing covers it: write a new card.
- A `related` card the session neither confirms nor contradicts: leave it alone.

Then record the session (step 5) with `upto` = the material's `session.to` and every card you created, updated or
superseded from it. Do this even when nothing was worth keeping (no cards), or the session comes back every run.

Material is clipped: answers at 800 characters (2000 for the last turn), tool calls at 200, results at 160, and
only the last 4 tool calls of a turn (`tools_dropped` counts the rest). Before copying a command or a value into a
card, read the exact line: `mnemo context <path> <lineno> --before 0 --after 0 --max-chars 0`. Do not write a card
from a guess, and do not write one from an agent relaying another session's output when you can read the original.

Each turn has a `ts` (UTC) and a `date` (this device's calendar date, the one cards use). Sessions can run for
days, so a later turn wins over an earlier one when they disagree. Write cards from what a session observed
itself (a command's output, a file it read); a claim it only relays from another session is not evidence, read
that session instead.

## What is worth a card

| type | keep | example |
|---|---|---|
| `fact` | a stable fact about the user's machines, accounts or setup that a future session would otherwise rediscover | NAS is `192.168.1.30`; its shares are mounted on the Mac mini at `~/nas/{docker,media,personal}` |
| `howto` | a procedure that worked, with the exact commands | delete from iCloud only after SHA-256 of original, NAS copy and manifest all match |
| `gotcha` | a failure, its cause and the fix | the Mac mini's remote shell is zsh: an unquoted `$a` does not split on spaces |
| `decision` | a choice made and why | iCloud backups go under `iCloudBackup/` on the NAS, mirroring the iCloud path |
| `preference` | how the user wants things done, often learned from a correction ("不是…", "不用管", "别…"); the rule itself, which holds beyond one task. The steps that carried it out are a separate `howto` | verify a backup before deleting anything |

Skip: one-off answers (a single lookup, a translation), anything found in the repo or its docs, chit-chat, and the
content of files or media; setup an agent can rediscover in one command (`mnemo doctor`, `which x`) unless finding
it was hard. Never put credentials, tokens, cookies, passkeys or personal documents' contents in a card. Say where
they live (a Keychain item's service name, a config file path) and use a placeholder like `$KEY` in commands.

A session can yield several cards, or none. One card holds one claim; split a turn that taught two things.

## Card file

`<kb dir>/<id>.md`, id in lowercase letters, digits and `-`, naming the claim (`mac-mini-addresses`, not `note-3`).

```markdown
---
type: fact
scope:
- host:laptop
sources:
- codex@laptop:/Users/alex/.codex/sessions/2026/11/17/rollout-….jsonl#456
created: 2026-11-26
verified: 2026-11-26
---
# NAS address and shares

The NAS is `192.168.1.30`. ...
```

With `save_card` you pass these as arguments and mnemo writes the file; by hand:

- Header: one `key: value` per line; list fields (`scope`, `sources`) with `- item` lines below the key; no quotes,
  no nesting. Required: `type`, `scope`, `sources`, `created`, `verified`. Optional: `expires`, `superseded_by`.
- `scope`: `global`, `host:<device name>` or `cwd:<absolute path>`; list several if it holds in several places.
  Use the narrowest that is true.
- `sources`: `<source>@<host>:<path>#<lineno>` from the material's `session`; the lineno of the message that
  carries the claim (the answer or the tool result that showed it), not the user's question. A claim backed in two
  places gets two entries.
- `expires`: only for facts with a known end, e.g. a deadline or an exam in progress. A snapshot without one
  ("not mounted as of 2026-11-17") states its date in the body instead.
- `verified`: the `date` of the latest turn that showed the claim true, not the day you distill. When a card
  holds details from several turns, the latest turn that showed it still holds. It only moves forward. `created`
  is the day the card was written.
- Body: a `# title` stating the claim, then the few lines a future agent needs to act on it. Commands verbatim.
  It states what is true now; do not append dated "checked again" lines, `sources` keeps that history. Put the
  distinctive names (hosts, paths, options, addresses) in backticks: `related` matches cards by them.
  Write in the language the user used in the session.
