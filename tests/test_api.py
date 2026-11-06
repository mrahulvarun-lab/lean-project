"""HTTP contract tests for the FastAPI endpoints."""

from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_recommendations_shape(client):
    r = client.get("/users/7/recommendations?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == 7
    assert body["strategy"] == "interest_based"
    assert body["count"] == len(body["recommendations"]) == 5
    rec = body["recommendations"][0]
    # contract fields present
    for key in ("circle_id", "name", "score", "why", "reasons"):
        assert key in rec
    assert rec["why"]
    # signals hidden unless debug=true
    assert rec["signals"] == {}


def test_recommendations_debug_exposes_signals(client):
    r = client.get("/users/7/recommendations?limit=1&debug=true")
    rec = r.json()["recommendations"][0]
    assert "health" in rec["signals"]
    assert "topic_score" in rec["signals"]


def test_cold_start_strategy_via_api(client):
    r = client.get("/users/42/recommendations")
    assert r.json()["strategy"] == "cold_start"


def test_duplicate_scenario_via_api(client):
    ids = [rec["circle_id"] for rec in client.get("/users/15/recommendations").json()["recommendations"]]
    assert len({10, 11, 12} & set(ids)) == 1


def test_unknown_user_404(client):
    assert client.get("/users/99999/recommendations").status_code == 404


def test_limit_validation_422(client):
    assert client.get("/users/7/recommendations?limit=0").status_code == 422
    assert client.get("/users/7/recommendations?limit=999").status_code == 422


def test_user_summary(client):
    body = client.get("/users/15").json()
    assert body["id"] == 15
    assert set(body["topic_interests"]) == {"negotiation", "confidence"}
