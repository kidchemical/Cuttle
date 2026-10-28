"""Secret vault (experimental flag ``secret_vault``, default off).

Lets agents use passwords, API keys and other secrets in commands without the
value ever entering the chat, the model context, or command output.

* ``core/``     — host-independent: storage backends, delivery, redaction, askpass.
* ``service``   — Cuttle adapter: metadata/approvals in ``<home>/secret_vault/``.
* ``cards``     — chat card specs for the ``secret`` / ``secret_approval`` fields.
* ``routes``    — owner-only HTTP; the Secrets App page.
* ``__main__``  — agent verbs (``python -m api.secret_vault``).

Teardown: delete the flag row in ``api/experimental/features.py``, this
package, its blueprint block in ``web_chat_api.py``, ``secrets_page.html`` +
``css/secrets_page.css`` + ``js/secret_vault/``, ``js/chat/chat_secret_fields.js``
(+ its ``<script>`` and two call sites in ``chat_action_forms.js`` /
``chat_action_cards.js``), the secret field cases in ``api.action_forms``, the
``nav-secrets`` rail entry, and ``.cuttle_global/features/secret_vault/``.
Stored values remain in the user's keyring/manager until they delete them.
"""
