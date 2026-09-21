"""End-to-end tests for the FastAPI /evaluate endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_evaluate_true():
    r = client.post(
        "/evaluate",
        json={
            "facts": {"age": 20, "country": "CN", "tier": "gold"},
            "rules": {
                "is_adult": {"gte": ["age", 18]},
                "domestic": {"eq": ["country", "CN"]},
                "eligible": {
                    "all": [
                        {"ref": "is_adult"},
                        {"ref": "domestic"},
                        {"not": {"eq": ["tier", "banned"]}},
                    ]
                },
            },
            "target": "eligible",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "true"
    assert body["result_known"] is True
    assert body["missing_fields"] == []
    assert body["used_facts"] == ["age", "country", "tier"]


def test_evaluate_unknown_and_diagnostics():
    r = client.post(
        "/evaluate",
        json={
            "facts": {"age": 20},
            "rules": {"eligible": {"all": [
                {"gte": ["age", 18]},
                {"eq": ["country", "CN"]},
            ]}},
            "target": "eligible",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "unknown"
    assert body["missing_fields"] == ["country"]
    assert body["used_facts"] == ["age", "country"]


def test_evaluate_short_circuit_stable():
    payload = {
        "facts": {"a": 0, "b": 2},
        "target": {"all": [{"eq": ["a", 1]}, {"eq": ["b", 2]}]},
    }
    r1 = client.post("/evaluate", json=payload).json()
    r2 = client.post("/evaluate", json=payload).json()
    assert r1 == r2
    assert r1["result"] == "false"
    paths = [p["path"] for p in r1["hit_paths"]]
    assert "$target/all[1]" not in paths


def test_missing_ref_returns_422():
    r = client.post(
        "/evaluate",
        json={"facts": {}, "rules": {"a": {"ref": "ghost"}}, "target": "a"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "rule_error"
    assert "ghost" in r.json()["detail"]


def test_cycle_returns_422():
    r = client.post(
        "/evaluate",
        json={
            "facts": {},
            "rules": {"a": {"ref": "b"}, "b": {"ref": "a"}},
            "target": "a",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "rule_error"
    assert "cyclic" in r.json()["detail"]


def test_malformed_body_returns_422():
    r = client.post("/evaluate", json={"facts": "not-an-object"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_request"


def test_inline_target():
    r = client.post(
        "/evaluate",
        json={
            "facts": {"x": 5},
            "target": {"any": [{"gt": ["x", 10]}, {"gt": ["x", 1]}]},
        },
    )
    assert r.status_code == 200
    assert r.json()["result"] == "true"
