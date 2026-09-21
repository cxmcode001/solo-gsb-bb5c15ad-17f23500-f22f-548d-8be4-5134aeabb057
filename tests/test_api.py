"""HTTP-level integration tests for POST /evaluate."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_evaluate_true_with_full_trace():
    payload = {
        "facts": {"age": 25, "consent": True},
        "rules": {
            "is_adult": {"gte": ["age", 18]},
            "pass": {
                "all": [
                    {"ref": "is_adult"},
                    {"eq": ["consent", True]},
                ]
            },
        },
        "target": "pass",
    }
    resp = client.post("/evaluate", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "result": "true",
        "used_facts": ["age", "consent"],
        "missing_fields": [],
        "hit_paths": [
            "is_adult",
            "is_adult.gte",
            "pass",
            "pass.all[0]",
            "pass.all[0].ref",
            "pass.all[1]",
            "pass.all[1].eq",
        ],
    }


def test_evaluate_unknown_when_fact_missing():
    payload = {
        "facts": {},
        "rules": {"minor": {"lt": ["age", 18]}},
        "target": "minor",
    }
    resp = client.post("/evaluate", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "unknown"
    assert body["missing_fields"] == ["age"]
    assert body["hit_paths"] == ["minor", "minor.lt"]


def test_missing_reference_returns_400():
    payload = {
        "facts": {},
        "rules": {"r": {"ref": "undefined_rule"}},
        "target": "r",
    }
    resp = client.post("/evaluate", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "missing_reference"
    assert body["details"][0]["missing_rule"] == "undefined_rule"


def test_cycle_returns_400():
    payload = {
        "facts": {},
        "rules": {"a": {"ref": "b"}, "b": {"ref": "a"}},
        "target": "a",
    }
    resp = client.post("/evaluate", json=payload)
    assert resp.status_code == 400
    assert resp.json()["error"] == "reference_cycle"


def test_unknown_target_returns_400():
    payload = {
        "facts": {},
        "rules": {"r": {"eq": ["x", 1]}},
        "target": "other",
    }
    resp = client.post("/evaluate", json=payload)
    assert resp.status_code == 400
    assert resp.json()["error"] == "unknown_target"


def test_malformed_rule_returns_400():
    payload = {
        "facts": {},
        "rules": {"r": {"wat": 1}},
        "target": "r",
    }
    resp = client.post("/evaluate", json=payload)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_rule_structure"


def test_request_body_validation_422():
    resp = client.post("/evaluate", json={"facts": {}})
    assert resp.status_code == 422


def test_short_circuit_does_not_drop_trace():
    payload = {
        "facts": {"a": True},
        "rules": {
            "r": {
                "any": [
                    {"eq": ["a", True]},
                    {"gt": ["missing_field", 3]},
                ]
            }
        },
        "target": "r",
    }
    body = client.post("/evaluate", json=payload).json()
    assert body["result"] == "true"
    assert body["missing_fields"] == ["missing_field"]
    assert body["hit_paths"] == [
        "r",
        "r.any[0]",
        "r.any[0].eq",
        "r.any[1]",
        "r.any[1].gt",
    ]


@pytest.mark.parametrize("repeat", range(3))
def test_responses_are_stable(repeat):
    payload = {
        "facts": {"age": 17},
        "rules": {
            "adult": {"gte": ["age", 18]},
            "needs_guardian": {"not": {"ref": "adult"}},
        },
        "target": "needs_guardian",
    }
    first = client.post("/evaluate", json=payload).json()
    second = client.post("/evaluate", json=payload).json()
    assert first == second
    assert first["result"] == "true"
