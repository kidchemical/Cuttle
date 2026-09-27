# Legacy worker — kept for reference. Prefer restart-flask.ps1 → POST /api/flask/restart.
# Direct taskkill is unsafe when the requester is a chat hosted by Flask.
Write-Output 'Deprecated: use POST /api/flask/restart (daemon-owned). No kill performed.'
exit 0
