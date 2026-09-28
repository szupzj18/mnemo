# Security policy

Mnemo indexes agent transcripts, which routinely contain source code, credentials pasted into prompts and internal URLs. Please treat security issues seriously and report them privately.

## Reporting a vulnerability

Use GitHub's **[private vulnerability reporting](https://github.com/szupzj18/mnemo/security/advisories/new)**. Please don't open a public issue. Include the version (`mnemo --version`), your OS, and the smallest reproduction you can build from synthetic data (`scripts/make-demo-home.py`).

You can expect an acknowledgement within a few days.

## Scope

In scope:

- The dashboard being reachable from anything other than the local machine, or API access without the per-launch token
- Remote execution paths that let a crafted query, path or session file run unintended commands on a local or remote host
- Session content leaving its device beyond the messages explicitly requested
- Path traversal through `context` / `session` / raw reads

Out of scope:

- Anyone with shell access to a machine being able to read its `~/.mnemo/index.db`. That's the same trust boundary as the agent logs themselves.
- Issues that require an already-compromised SSH configuration

## Hardening notes for users

- `~/.mnemo/index.db` stores transcripts in plaintext. Keep your home directory permissions tight, and exclude the file from cloud sync if your logs are sensitive.
- Register remotes only on hosts you already trust with SSH access to your account.
