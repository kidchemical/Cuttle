"""Host-independent secret vault core (stdlib only, no Cuttle imports).

Contract (v1):

* ``backends`` — a :class:`~.backends.Backend` stores or reads a value by
  reference (OS keyring via ``keyring``, an external CLI such as ``pass`` /
  ``op`` / ``bw`` / ``secret-tool``, or memory for tests).
* ``deliver`` — runs a child command with secrets injected through env vars,
  stdin, temp files, or askpass prompts (ssh / sudo / git), never argv.
* ``redact`` — streaming byte redaction of injected values from child output.
* ``askpass`` — the helper executable programs call for a password.

The host (Cuttle adapter in ``api.secret_vault.service``) owns metadata,
approval, configuration and which backend serves which secret.
"""

CONTRACT_VERSION = 1
