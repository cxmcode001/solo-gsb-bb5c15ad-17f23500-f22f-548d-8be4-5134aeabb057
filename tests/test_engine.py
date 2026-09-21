"""Unit tests for the three-valued rule engine."""

import pytest

from app import engine


# --------------------------------------------------------------------------- #
# Basic operators
# --------------------------------------------------------------------------- #


def test_eq_true_and_false():
    rules = {}
    target = {"eq": ["country", "CN"]}
    engine.analyze(rules, target)
    out = engine.evaluate(rules, {"country": "CN"}, target)
    assert out["result"] == "true"
    assert out["used_facts"] == ["country"]
    assert out["missing_fields"] == []


def test_missing_fact_is_unknown_not_false():
    target = {"eq": ["country", "CN"]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {}, target)
    assert out["result"] == "unknown"
    assert out["result_known"] is False
    assert out["missing_fields"] == ["country"]
    assert out["used_facts"] == ["country"]


def test_ne_unknown_when_missing():
    target = {"ne": ["country", "CN"]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {}, target)
    assert out["result"] == "unknown"


def test_numeric_comparisons():
    rules = {}
    target = {"all": [
        {"gt": ["age", 17]},
        {"gte": ["age", 18]},
        {"lt": ["score", 100]},
        {"lte": ["score", 99]},
        {"ne": ["name", ""]},
    ]}
    engine.analyze(rules, target)
    out = engine.evaluate(rules, {"age": 18, "score": 99, "name": "acme"}, target)
    assert out["result"] == "true"


def test_incomparable_types_are_unknown():
    target = {"gt": ["age", 18]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"age": "not-a-number"}, target)
    assert out["result"] == "unknown"
    assert out["missing_fields"] == []  # provided, just incomparable


def test_null_ordering_is_unknown():
    target = {"gt": ["age", 18]}
    engine.analyze({}, target)
    assert engine.evaluate({}, {"age": None}, target)["result"] == "unknown"
    target_eq = {"eq": ["age", None]}
    engine.analyze({}, target_eq)
    assert engine.evaluate({}, {"age": None}, target_eq)["result"] == "true"


# --------------------------------------------------------------------------- #
# Kleene three-valued logic
# --------------------------------------------------------------------------- #


def test_not_truth_table():
    for raw, expected in [(True, False), (False, True), (None, None)]:
        target = {"not": {"eq": ["x", 1]}}
        engine.analyze({}, target)
        facts = {"x": 1} if raw is True else ({"x": 2} if raw is False else {})
        assert engine.evaluate({}, facts, target)["result"] == engine._label(expected)


def test_all_unknown_propagation_without_false():
    # true AND unknown => unknown
    target = {"all": [{"eq": ["a", 1]}, {"eq": ["b", 2]}]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"a": 1}, target)
    assert out["result"] == "unknown"
    assert out["missing_fields"] == ["b"]


def test_any_unknown_propagation_without_true():
    # false OR unknown => unknown
    target = {"any": [{"eq": ["a", 1]}, {"eq": ["b", 2]}]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"a": 9}, target)
    assert out["result"] == "unknown"


def test_all_false_dominates_unknown():
    # unknown AND false => false (false wins even when a sibling is unknown)
    target = {"all": [{"eq": ["missing", 1]}, {"eq": ["a", 2]}]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"a": 1}, target)
    assert out["result"] == "false"


def test_any_true_dominates_unknown():
    target = {"any": [{"eq": ["missing", 1]}, {"eq": ["a", 1]}]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"a": 1}, target)
    assert out["result"] == "true"


# --------------------------------------------------------------------------- #
# Short circuit and hit paths
# --------------------------------------------------------------------------- #


def test_all_short_circuits_and_reports_traversed_paths():
    target = {"all": [
        {"eq": ["a", 1]},  # false -> short circuit
        {"eq": ["b", 2]},  # never evaluated
    ]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"a": 0, "b": 2}, target)
    assert out["result"] == "false"
    paths = [p["path"] for p in out["hit_paths"]]
    assert "$target/all[0]" in paths
    assert "$target/all[1]" not in paths
    # b was never read because of short-circuit
    assert "b" not in out["used_facts"]
    assert out["missing_fields"] == []


def test_any_short_circuits():
    target = {"any": [
        {"eq": ["a", 1]},  # true -> short circuit
        {"eq": ["b", 2]},
    ]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"a": 1}, target)
    assert out["result"] == "true"
    paths = [p["path"] for p in out["hit_paths"]]
    assert "$target/any[1]" not in paths
    assert "b" not in out["used_facts"]


def test_hit_paths_carry_result_and_field_metadata():
    target = {"all": [{"gt": ["age", 18]}]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"age": 30}, target)
    leaf = next(p for p in out["hit_paths"] if p["kind"] == "compare")
    assert leaf["field"] == "age"
    assert leaf["expected"] == 18
    assert leaf["result"] == "true"


def test_hit_paths_are_in_deterministic_traversal_order():
    target = {"all": [{"eq": ["a", 1]}, {"eq": ["b", 2]}]}
    engine.analyze({}, target)
    facts = {"a": 1, "b": 2}
    first = engine.evaluate({}, facts, target)["hit_paths"]
    second = engine.evaluate({}, facts, target)["hit_paths"]
    assert [p["path"] for p in first] == [p["path"] for p in second]


def test_used_and_missing_sets_are_sorted():
    target = {"all": [{"eq": ["z", 1]}, {"eq": ["a", 2]}, {"eq": ["m", 3]}]}
    engine.analyze({}, target)
    out = engine.evaluate({}, {"z": 1}, target)
    assert out["used_facts"] == ["a", "m", "z"]
    assert out["missing_fields"] == ["a", "m"]


# --------------------------------------------------------------------------- #
# Refs, missing references and cycles
# --------------------------------------------------------------------------- #


def test_named_rule_ref_resolves():
    rules = {
        "is_adult": {"gte": ["age", 18]},
        "target_rule": {"all": [
            {"ref": "is_adult"},
            {"eq": ["country", "CN"]},
        ]},
    }
    target = "target_rule"
    engine.analyze(rules, target)
    out = engine.evaluate(rules, {"age": 20, "country": "CN"}, target)
    assert out["result"] == "true"
    assert any(p.get("ref") == "target_rule" for p in out["hit_paths"])
    assert any(p.get("ref") == "is_adult" for p in out["hit_paths"])


def test_inline_target_with_refs():
    rules = {"r1": {"eq": ["a", 1]}}
    target = {"ref": "r1"}
    engine.analyze(rules, target)
    assert engine.evaluate(rules, {"a": 1}, target)["result"] == "true"


def test_missing_reference_rejected():
    rules = {"a": {"ref": "ghost"}}
    with pytest.raises(engine.RuleError, match="missing referenced rule"):
        engine.analyze(rules, "a")


def test_missing_target_name_rejected():
    with pytest.raises(engine.RuleError, match="missing referenced rule"):
        engine.analyze({}, "nope")


def test_direct_cycle_rejected():
    rules = {"a": {"ref": "b"}, "b": {"ref": "a"}}
    with pytest.raises(engine.RuleError, match="cyclic rule reference"):
        engine.analyze(rules, "a")


def test_self_cycle_rejected():
    rules = {"a": {"ref": "a"}}
    with pytest.raises(engine.RuleError, match="cyclic rule reference"):
        engine.analyze(rules, "a")


def test_indirect_cycle_rejected():
    rules = {
        "a": {"ref": "b"},
        "b": {"ref": "c"},
        "c": {"ref": "a"},
    }
    with pytest.raises(engine.RuleError, match="a -> b -> c -> a"):
        engine.analyze(rules, "a")


def test_analysis_runs_before_evaluation():
    # Even with a cycle, no evaluation occurs.
    rules = {"a": {"all": [{"ref": "b"}, {"eq": ["x", 1]}]}, "b": {"ref": "a"}}
    with pytest.raises(engine.RuleError):
        engine.analyze(rules, "a")


def test_diamond_dag_is_not_a_cycle():
    rules = {
        "top": {"all": [{"ref": "l"}, {"ref": "r"}]},
        "l": {"ref": "base"},
        "r": {"ref": "base"},
        "base": {"eq": ["x", 1]},
    }
    engine.analyze(rules, "top")
    out = engine.evaluate(rules, {"x": 1}, "top")
    assert out["result"] == "true"


# --------------------------------------------------------------------------- #
# Structural validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad",
    [
        {"all": "nope"},
        {"not": []},
        {"ref": ""},
        {"ref": 5},
        {"eq": ["only"]},
        {"eq": [1, 2]},
        {"eq": "x"},
        {"foo": ["x", 1]},
        {"eq": ["x", 1], "ne": ["x", 2]},
        ["not-a-node"],
        "not-a-node",
    ],
)
def test_malformed_nodes_rejected(bad):
    with pytest.raises(engine.RuleError):
        engine.analyze({}, bad)


def test_unknown_extra_key_rejected():
    with pytest.raises(engine.RuleError, match="unsupported keys"):
        engine.analyze({}, {"eq": ["x", 1], "extra": 2})


def test_rules_must_be_object():
    with pytest.raises(engine.RuleError, match="'rules'"):
        engine.analyze([], {"eq": ["x", 1]})
