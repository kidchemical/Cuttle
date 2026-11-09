"""Contract coverage for the replaceable curated local-AI dashboard catalog."""

from flask import Flask

from api.dashboards import catalog, local_ai, service
from api.dashboards.routes import dashboards_bp


def test_local_ai_catalog_payloads_have_ranked_rows_filters_and_rank_axis():
    for dash_id in local_ai.dashboard_ids():
        payload = local_ai.dashboard(dash_id)
        assert payload["success"] is True
        assert payload["catalog_schema_version"] == 1
        assert "overall_rank" in payload["axes"]
        assert "overall_score" in payload["axes"]
        assert payload["ranking"]["weights"]
        assert [row["overall_rank"] for row in payload["rows"]] == list(range(1, len(payload["rows"]) + 1))
        assert all("sources" in row or "listing" in row for row in payload["rows"])


def test_local_ai_builds_expose_purchase_listing_and_every_advertised_part_link():
    payload = local_ai.dashboard("local-ai-builds")
    assert len(payload["rows"]) >= 5
    for build in payload["rows"]:
        assert build["listing"]["url"].startswith("https://")
        assert build["parts"]
        assert all(part["url"].startswith("https://") for part in build["parts"])
        assert build["availability"]
        assert build["verified_at"]


def test_local_ai_dashboard_cards_are_public_and_service_uses_catalog_contract():
    cards = {card["id"]: card for card in catalog.list_dashboards()}
    for dash_id in local_ai.dashboard_ids():
        assert cards[dash_id]["public"] is True
        assert service.local_ai_dashboard(dash_id)["id"] == dash_id


def test_local_ai_dashboard_routes_are_available_to_signed_in_guest(owner_session):
    app = Flask(__name__)
    app.register_blueprint(dashboards_bp)
    client = app.test_client()
    db = owner_session.db
    guest_id = db.create_user("local-ai-guest@local", "Local AI guest", "guest", password="x")
    client.set_cookie("session_token", db.create_auth_session(guest_id))
    for dash_id in local_ai.dashboard_ids():
        response = client.get("/api/dashboards/" + dash_id)
        assert response.status_code == 200
        assert response.get_json()["id"] == dash_id
