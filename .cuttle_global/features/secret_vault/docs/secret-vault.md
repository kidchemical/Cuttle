# Secret vault

Use this whenever a command needs a password, passphrase, API key, token or
other credential. The user types the value into a Cuttle secret field or the
**Apps → Secrets** page. You refer to it only by name; the value goes to the
child process and is redacted from its output.

Hard rules:

- Never ask for a secret in plain chat text or in a normal form field. Never
  echo, log, `printenv`, `cat` or otherwise reveal one, and never put one in
  argv.
- Never read the keyring or password manager yourself, and never pass
  `--no-redact` unless the user asked for it.
- When `run` prints a card, emit it **verbatim** in your reply and end the
  turn. Rerun the same command after the user answers.

## Verbs

```bash
python -m api.secret_vault list                 # names + metadata (never values)
python -m api.secret_vault status               # backends, approval policy
python -m api.secret_vault request NAME [NAME…] [--label "NAME=prompt text"] [--reason "…"]
python -m api.secret_vault run [deliveries…] -- COMMAND ARGS…
```

The calling chat (`CUTTLE_CHAT_SESSION_ID`) is picked up automatically.

## Deliveries

| Need | Flag | Example |
|---|---|---|
| ssh / scp / sftp / rsync password or key passphrase | `--askpass NAME` | `run --askpass prod-ssh -- ssh deploy@prod uptime` |
| sudo (Linux/macOS; `-A` is added) | `--askpass NAME` | `run --askpass root-pw -- sudo systemctl restart nginx` |
| git over HTTPS | `--askpass` per prompt | `run --askpass gh-user=Username --askpass gh-token=Password -- git push` |
| API key / env var | `--env VAR=NAME` | `run --env OPENAI_API_KEY=openai -- python job.py` |
| tool reads stdin | `--stdin NAME` | `run --stdin ghcr -- docker login -u me --password-stdin ghcr.io` |
| tool needs a file path | `--file VAR=NAME` | `run --file KUBECONFIG=kube-prod -- kubectl get pods` |

Combine flags freely. With several `--askpass` secrets, give each a prompt
regex (`NAME=REGEX`). Askpass refuses host-key yes/no prompts unless one is
matched explicitly, and answers each prompt at most a few times per run, so
a wrong password is not retried into a lockout. New hosts: accept the host key
first (`ssh -o StrictHostKeyChecking=accept-new …`) or ask the user.

## Exit codes and cards

| Exit | Meaning | What you do |
|---|---|---|
| child's code | ran | report output (values show as `[secret:NAME]`) |
| 4 | secret not stored yet | emit the printed **request** card (password field) and end the turn |
| 3 | approval needed | emit the printed **approval** card (exact command shown) and end the turn |
| 2 | usage, project scope, or feature off | fix the call. If the feature is off, tell the user |
| 5 | backend error (locked keyring, manager CLI signed out) | tell the user what the error says |

To ask for a value ahead of time, run `request`. To ask inside your own form,
add a field `{"id":"pw","type":"secret","secret":"NAME","label":"…","required":true}`
to a Q&A `cuttle_action_form` (`"resume": true`). The answer you get back is
only `saved to the vault as secret:NAME`. Entering a value in a chat also
approves that chat to use it.

Pick short descriptive names (`prod-ssh`, `openai`, `gh-token`). Store
usernames and hostnames in plain text, not in the vault.

## Limits

The approval check runs inside Cuttle's own CLI. It is a consent record, not a
sandbox: an agent running as the same OS user could still reach the keyring
directly. These rules are what keep values out of transcripts. Interactive
TUIs that read `/dev/tty` directly (`passwd`, `su`) are not supported. Use
`sudo -A`, keys, or a tool's stdin/env/file option instead.
