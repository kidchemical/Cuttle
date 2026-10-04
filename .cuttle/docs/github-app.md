# GitHub App commit attribution

Settings → Account → GitHub App accepts an App ID, installation ID, and an
unencrypted RSA PEM private key. Save, then Re-check to verify the installation
and resolve the app's bot user ID. Previously saved configurations need a fresh
Re-check before they can attribute commits.

The private key stays in `.cuttle/personal/secrets/github_app_key.pem` (ignored,
mode 0600). Public identifiers and verified identity facts live in the
`github_app` settings entry. Invalid keys are rejected before replacing existing
credentials; failed settings writes restore the previous key.

Cuttle's Git commit controls and task close-via-commit use the verified bot as
both author and committer when `origin` points to github.com and its owner
matches the verified installation account. HTTPS, SSH, and SCP-style remotes
are supported. Other hosts/accounts and repositories without an origin retain
existing identity behavior. A configured app without current verification
blocks GitHub commits before staging and asks for Re-check.

Attribution uses environment variables for each commit; repository and global
Git configuration are unchanged. Shell commits outside these Cuttle controls
keep their normal identity. Commit creation needs no network request.

This feature does not change push authentication or cryptographically sign
commits. Existing push approval and authentication still apply.

Owner: `src/api/github_app.py`; settings endpoints belong to
`src/api/settings_routes.py`; callers are `src/api/git_service.py` and
`src/scripts/utilities/git_pending_changes.py`. Verification resolves the bot
user ID separately from the App ID, following GitHub's maintained example:
https://github.com/actions/create-github-app-token/blob/main/README.md
