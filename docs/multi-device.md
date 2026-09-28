# Multi-device

Mnemo lets the agent on your laptop recall a session that ran on a devbox, and the other way around. It does this without a central server and without copying transcripts between machines.

## Design: message passing, not shared storage

> Don't communicate by sharing memory; share memory by communicating.

- **Each machine indexes only its own logs.** Every device has its own `~/.mnemo/index.db`.
- **A search is a message.** The query goes over SSH to every registered device in parallel. Each device searches locally and returns its ranked hits.
- **Results are merged by rank, not score.** BM25 scores from different corpora aren't comparable, so Mnemo merges them with Reciprocal Rank Fusion (`k = 60`).
- **Reads go to the data.** `context`, `session` and raw reads run on the device that holds the file. Only the messages you asked for travel back.

Adding a device means no data migration, and removing one leaves nothing behind on the other machines.

## Add a device

Requirements on the remote: passwordless SSH (keys or Kerberos/GSSAPI), `rsync`, and Python 3.7+ with SQLite FTS5.

```bash
mnemo remote add devbox-126                  # ssh host = name
mnemo remote add gpu-box user@10.0.0.12      # explicit ssh target
```

`remote add` does the following:

1. Checks connectivity.
2. Uses `rsync` to copy the Mnemo code to `~/mnemo/` on the remote. It excludes `.git`, caches and `index.db`.
3. Runs `mnemo index` there to build the remote index.
4. Registers the device in `~/.mnemo/remotes.json`.

After you upgrade Mnemo locally, run `mnemo remote update` to push the new code to every remote.

## Form a mesh

Remotes are configured per machine. So that every machine can search every other, run `remote add` on each one:

```bash
# on the laptop
mnemo remote add devbox-109 && mnemo remote add devbox-126
# on devbox-109
mnemo remote add devbox-126
# on devbox-126
mnemo remote add devbox-109
```

Forwarded searches are pinned to `--host local`, so a device answers only from its own index. Queries never chain through the mesh, and hits are never duplicated.

## Freshness and failure

- Before each federated search, every remote runs an incremental `mnemo index`. This takes well under a second when idle.
- SSH connections are multiplexed with `ControlMaster` (sockets in `~/.mnemo/ssh-<name>`, persisting 10 minutes), so only the first query pays the handshake.
- `ConnectTimeout` is 8 s and `BatchMode` is on. An unreachable or password-prompting device is skipped with a warning, and results from the other devices come back complete.
- The dashboard's device page shows per-device latency, index stats and a connectivity test.

## Kerberos notes

On hosts whose system `krb5.conf` lacks your corporate realm (a stock MIT config, for example), put a working config at `~/.krb5.conf`. Mnemo points its SSH calls at it automatically through `KRB5_CONFIG` when the file exists. Make sure you have a valid ticket (`klist`) before searching.

## Security model

- Mnemo opens no ports on remotes. Everything goes through your existing SSH trust.
- A remote returns only search hits and the messages you explicitly read. The full index and raw logs never leave it.
- Anyone who can SSH into a device can already read its logs, so Mnemo doesn't widen that boundary.
