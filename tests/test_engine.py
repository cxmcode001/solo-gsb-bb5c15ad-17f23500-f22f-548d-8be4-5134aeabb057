"""Tests for the three-valued rule engine (no network / framework needed)."""

import pytest

from app.engine import (
    FALSE,
    TRUE,
    UNKNOWN,
    RuleError,
    evaluate,
    validate_rules,
)


# ---------------------------------------------------------------------------
# Basic comparison operators
# ---------------------------------------------------------------------------


def test_eq_ne_gt_lte_simple():
    rules = {"r": {"gte": ["age", 18]}}
    assert evaluate(rules, {"age": 20}, "r")["result"] == TRUE
    assert evaluate(rules, {"age": 18}, "r")["result"] == TRUE
    assert evaluate(rules, {"age": 17}, "r")["result"] == FALSE
    assert evaluate({"r": {"lt": ["age", 18]}}, {"age": 17}, "r")["result"] == TRUE
    assert evaluate({"r": {"eq": ["name", "ada"]}}, {"name": "ada"}, "r")["result"] == TRUE
    assert evaluate({"r": {"ne": ["name", "ada"]}}, {"name": "bob"}, "r")["result"] == TRUE


def test_missing_fact_is_unknown_and_traced():
    out = evaluate({"r": {"gte": ["age", 18]}}, {}, "r")
    assert out["result"] == UNKNOWN
    assert out["used_facts"] == []
    assert out["missing_fields"] == ["age"]
    assert out["hit_paths"] == ["r", "r.gte"]


def test_dotted_nested_fact_paths():
    rules = {"r": {"eq": ["user.region", "CN"]}}
    out = evaluate(rules, {"user": {"region": "CN"}}, "r")
    assert out["result"] == TRUE
    assert out["used_facts"] == ["user.region"]

    out = evaluate(rules, {"user": {}}, "r")
    assert out["result"] == UNKNOWN
    assert out["missing_fields"] == ["user.region"]

    # null intermediate object means the remaining segment cannot resolve
    out = evaluate(rules, {"user": None}, "r")
    assert out["result"] == UNKNOWN
    assert out["missing_fields"] == ["user.region"]


def test_explicit_null_is_present_but_unordered():
    assert evaluate({"r": {"eq": ["x", None]}}, {"x": None}, "r")["result"] == TRUE
    assert evaluate({"r": {"ne": ["x", None]}}, {"x": None}, "r")["result"] == FALSE
    assert evaluate({"r": {"gte": ["x", 1]}}, {"x": None}, "r")["result"] == UNKNOWN


# ---------------------------------------------------------------------------
# Ternary Kleene logic
# ---------------------------------------------------------------------------


def test_not_unknown_is_unknown():
    rules = {"r": {"not": {"gt": ["age", 65]}}}
    assert evaluate(rules, {"age": 80}, "r")["result"] == FALSE
    assert evaluate(rules, {}, "r")["result"] == UNKNOWN


def test_all_kleene_truth_table():
    rules = {
        "r": {
            "all": [
                {"eq": ["a", True]},
                {"eq": ["b", True]},
            ]
        }
    }
    assert evaluate(rules, {"a": True, "b": True}, "r")["result"] == TRUE
    assert evaluate(rules, {"a": True, "b": False}, "r")["result"] == FALSE
    assert evaluate(rules, {"a": False}, "r")["result"] == FALSE  # false dominates
    assert evaluate(rules, {"a": True}, "r")["result"] == UNKNOWN  # no false, one unknown


def test_any_kleene_truth_table():
    rules = {"r": {"any": [{"eq": ["a", True]}, {"eq": ["b", True]}]}}
    assert evaluate(rules, {"a": True}, "r")["result"] == TRUE  # true dominates
    assert evaluate(rules, {"a": False, "b": False}, "r")["result"] == FALSE
    assert evaluate(rules, {"a": False}, "r")["result"] == UNKNOWN  # no true, one unknown


def test_type_mismatch_ordering_is_unknown_equality_is_false():
    assert evaluate({"r": {"gt": ["a", 1]}}, {"a": "1"}, "r")["result"] == UNKNOWN
    assert evaluate({"r": {"eq": ["a", 1]}}, {"a": "1"}, "r")["result"] == FALSE
    # booleans are never numerically compared
    assert evaluate({"r": {"eq": ["a", 1]}}, {"a": True}, "r")["result"] == FALSE
    assert evaluate({"r": {"gte": ["a", 1]}}, {"a": True}, "r")["result"] == UNKNOWN


def test_numeric_cross_type_equality():
    assert evaluate({"r": {"eq": ["a", 1]}}, {"a": 1.0}, "r")["result"] == TRUE


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_ref_resolution_and_paths():
    rules = {
        "is_adult": {"gte": ["age", 18]},
        "eligible": {"all": [{"ref": "is_adult"}, {"eq": ["consent", True]}]},
    }
    facts = {"age": 20, "consent": True}
    out = evaluate(rules, facts, "eligible")
    assert out["result"] == TRUE
    assert out["used_facts"] == ["age", "consent"]
    assert out["hit_paths"] == [
        "eligible",
        "eligible.all[0]",
        "eligible.all[0].ref",
        "eligible.all[1]",
        "eligible.all[1].eq",
        "is_adult",
        "is_adult.gte",
    ]


def test_ref_through_not_records_every_rule():
    rules = {"a": {"not": {"ref": "b"}}, "b": {"eq": ["x", 1]}}
    out = evaluate(rules, {"x": 1}, "a")
    assert out["result"] == FALSE
    assert out["hit_paths"] == [
        "a",
        "a.not",
        "a.not.ref",
        "b",
        "b.eq",
    ]


def test_missing_reference_rejected_before_evaluation():
    rules = {"r": {"all": [{"ref": "ghost"}]}}
    with pytest.raises(RuleError) as exc:
        validate_rules(rules)
    assert exc.value.code == "missing_reference"
    assert exc.value.details[0]["missing_rule"] == "ghost"
    assert exc.value.details[0]["at"] == "r.all[0].ref"


def test_self_cycle_rejected():
    rules = {"r": {"ref": "r"}}
    with pytest.raises(RuleError) as exc:
        validate_rules(rules)
    assert exc.value.code == "reference_cycle"
    assert exc.value.details[0]["cycle"] == ["r", "r"]


def test_two_node_and_long_cycles_deduplicated():
    rules = {
        "a": {"ref": "b"},
        "b": {"ref": "a"},
    }
    with pytest.raises(RuleError) as exc:
        validate_rules(rules)
    assert exc.value.code == "reference_cycle"
    # normalized to start at the smallest name, exactly one representation
    assert exc.value.details == [{"cycle": ["a", "b", "a"]}]

    rules = {"a": {"ref": "b"}, "b": {"ref": "c"}, "c": {"ref": "a"}}
    with pytest.raises(RuleError) as exc:
        validate_rules(rules)
    assert exc.value.details[0]["cycle"] == ["a", "b", "c", "a"]


def test_multiple_independent_cycles_sorted():
    rules = {
        "a": {"ref": "b"}, "b": {"ref": "a"},
        "c": {"ref": "d"}, "d": {"ref": "c"},
    }
    with pytest.raises(RuleError) as exc:
        validate_rules(rules)
    assert [d["cycle"] for d in exc.value.details] == [
        ["a", "b", "a"],
        ["c", "d", "c"],
    ]


def test_diamond_dag_is_not_a_cycle():
    rules = {
        "top": {"all": [{"ref": "l"}, {"ref": "r"}]},
        "l": {"ref": "base"},
        "r": {"ref": "base"},
        "base": {"eq": ["x", True]},
    }
    out = evaluate(rules, {"x": True}, "top")
    assert out["result"] == TRUE
    # shared base body appears once, facts collected once
    assert out["hit_paths"].count("base.eq") == 1
    assert out["used_facts"] == ["x"]


def test_unknown_target_rule():
    with pytest.raises(RuleError) as exc:
        evaluate({"r": {"eq": ["x", 1]}}, {}, "nope")
    assert exc.value.code == "unknown_target"


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def test_malformed_nodes_collected_with_paths():
    rules = {
        "r": {
            "all": [
                {"eq": ["age"]},             # missing literal
                {"bogus": 1},                # unknown operator
                {"all": [{"eq": "x", "not": {"eq": ["y", 1]}}]},  # two operators
            ]
        }
    }
    with pytest.raises(RuleError) as exc:
        validate_rules(rules)
    assert exc.value.code == "invalid_rule_structure"
    paths = {d["path"] for d in exc.value.details}
    assert "r.all[0].eq" in paths
    assert "r.all[1]" in paths
    assert "r.all[2].all[0]" in paths


def test_empty_all_any_rejected():
    with pytest.raises(RuleError) as exc:
        validate_rules({"r": {"all": []}})
    assert exc.value.code == "invalid_rule_structure"


def test_non_dict_rule_rejected():
    with pytest.raises(RuleError) as exc:
        validate_rules({"r": 42})  # type: ignore[dict-item]
    assert exc.value.code == "invalid_rule_structure"


def test_bad_ref_and_bad_literal_rejected():
    with pytest.raises(RuleError) as exc:
        validate_rules({"r": {"ref": 1}})
    assert exc.value.code == "invalid_rule_structure"
    with pytest.raises(RuleError) as exc:
        validate_rules({"r": {"eq": ["x", ["nested"]]}})
    assert exc.value.code == "invalid_rule_structure"


# ---------------------------------------------------------------------------
# Determinism / completeness under short-circuit semantics
# ---------------------------------------------------------------------------


def test_short_circuit_still_traces_every_branch():
    # AND: first branch is false (would short-circuit), second branch
    # references a missing field. It must still be visited and reported.
    rules = {
        "r": {
            "all": [
                {"eq": ["a", True]},
                {"gt": ["b", 10]},
            ]
        }
    }
    out = evaluate(rules, {"a": False}, "r")
    assert out["result"] == FALSE
    assert out["missing_fields"] == ["b"]
    assert out["hit_paths"] == [
        "r",
        "r.all[0]",
        "r.all[0].eq",
        "r.all[1]",
        "r.all[1].gt",
    ]
    assert out["used_facts"] == ["a"]


def test_any_short_circuit_traces_all_branches():
    rules = {
        "r": {
            "any": [
                {"eq": ["a", True]},
                {"not": {"gt": ["b", 10]}},
            ]
        }
    }
    out = evaluate(rules, {"a": True, "b": 5}, "r")
    assert out["result"] == TRUE
    # second branch visited despite true-dominant OR
    assert out["hit_paths"] == [
        "r",
        "r.any[0]",
        "r.any[0].eq",
        "r.any[1]",
        "r.any[1].not",
        "r.any[1].not.gt",
    ]
    assert out["used_facts"] == ["a", "b"]


def test_repeated_request_is_identical():
    rules = {
        "a": {"any": [{"gt": ["x", 1]}, {"ref": "b"}]},
        "b": {"all": [{"lt": ["y", 5]}, {"ref": "c"}]},
        "c": {"eq": ["z", "ok"]},
    }
    facts = {"x": 0}
    first = evaluate(rules, facts, "a")
    second = evaluate(rules, facts, "a")
    assert first == second
    assert first["result"] == UNKNOWN
    assert first["missing_fields"] == ["y", "z"]


def test_only_referenced_missing_fields_reported():
    # field present in facts but never read by a rule -> never in trace
    out = evaluate({"r": {"eq": ["a", 1]}}, {"a": 1, "ignored": 99}, "r")
    assert out["used_facts"] == ["a"]
    assert out["missing_fields"] == []


def test_unknown_propagates_through_reference_chain():
    rules = {"adult": {"gte": ["age", 18]}, "minor": {"not": {"ref": "adult"}}}
    out = evaluate(rules, {}, "minor")
    assert out["result"] == UNKNOWN
    assert out["missing_fields"] == ["age"]
