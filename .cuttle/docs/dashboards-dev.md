# Adding a dashboard (Cuttle repo)

Usage runbook (global, every project): `.cuttle_global/docs/dashboards.md`.

To add a dashboard to Cuttle itself:

1. Register a card in `src/api/dashboards/catalog.py`
2. Return a payload from `service.py` + a branch in `routes.py`
3. Handle `?d=<id>` in `src/web/js/dashboards_page.js`
