"""Three-valued compliance rule engine.

Rule nodes are plain JSON-compatible dicts. Exactly one of the following
operator keys is allowed per node:

  * {"all": [node, ...]}   - logical AND (Kleene)
  * {"any": [node, ...]}   - logical OR  (Kleene)
  * {"not": node}          - logical negation
  * {"ref": "rule_name"}   - reference to a named rule
  * {op: [field, literal]} - fact comparison, op in:
                             eq / ne / gt / gte / lt / lte

Evaluation uses three-valued logic (true / false / unknown) following
Kleene semantics:

    not unknown     -> unknown
    all: false dominates; true only if every child is true
    any: true  dominates; false only if every child is false

A comparison against a field that is absent from the facts yields
"unknown". Type-incompatible comparisons (e.g. ordering numbers against
strings, or comparing booleans) also yield "unknown"; only JSON equality is
allowed across numeric types.

Although the *result* follows the same semantics as short-circuit
evaluation, the evaluator visits every reachable node so that the trace
(used facts, missing fields, hit rule paths) is complete. All trace lists
are sorted, so identical requests always produce identical responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Ternary logic constants
# ---------------------------------------------------------------------------

TRUE = "true"
FALSE = "false"
UNKNOWN = "unknown"

LOGIC_KEYS = ("all", "any", "not", "ref")
COMPARE_OPS = ("eq", "ne", "gt", "gte", "lt", "lte")
VALID_KEYS = LOGIC_KEYS + COMPARE_OPS


class RuleError(ValueError):
    """Any rule-set or evaluation request problem the client must fix."""

    def __init__(self, code: str, message: str, details: list[Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


# ---------------------------------------------------------------------------
# Validation: structural checks, dangling references, reference cycles
# ---------------------------------------------------------------------------


def _issue(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


def _is_literal(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, (str, bool, int, float)) and not isinstance(value, (list, dict))


def _validate_node(node: Any, path: str, issues: list[dict[str, str]]) -> None:
    if not isinstance(node, dict):
        issues.append(_issue(path, "rule node must be an object"))
        return

    keys = [k for k in node.keys() if k in VALID_KEYS]
    if not keys:
        issues.append(
            _issue(path, "rule node must contain one operator key: " + ", ".join(VALID_KEYS))
        )
        return
    if len(keys) > 1:
        issues.append(
            _issue(
                path,
                "rule node must contain exactly one operator key, got: " + ", ".join(sorted(keys)),
            )
        )
        return

    key = keys[0]

    if key in ("all", "any"):
        children = node[key]
        if not isinstance(children, list) or not children:
            issues.append(
                _issue(f"{path}.{key}", f'"{key}" requires a non-empty list of nodes')
            )
            return
        for index, child in enumerate(children):
            _validate_node(child, f"{path}.{key}[{index}]", issues)
        return

    if key == "not":
        _validate_node(node["not"], f"{path}.not", issues)
        return

    if key == "ref":
        target = node["ref"]
        if not isinstance(target, str) or not target:
            issues.append(_issue(f"{path}.ref", '"ref" requires a non-empty rule name string'))
        return

    # Comparison operators: {"op": [field, literal]}
    operands = node[key]
    if not isinstance(operands, list) or len(operands) != 2:
        issues.append(_issue(f"{path}.{key}", f'"{key}" requires a [field, literal] pair'))
        return
    field_path, literal = operands
    if not isinstance(field_path, str) or not field_path:
        issues.append(_issue(f"{path}.{key}", "comparison field must be a non-empty string"))
        return
    if not _is_literal(literal):
        issues.append(
            _issue(
                f"{path}.{key}",
                "comparison literal must be a string, number, boolean or null",
            )
        )


def _scan_references(node: dict[str, Any], path: str, refs: list[tuple[str, str]]) -> None:
    """Collect (ref_name, node_path) pairs for every ref node reachable."""
    key = next((k for k in node if k in VALID_KEYS), None)
    if key is None:
        return
    if key in ("all", "any"):
        for index, child in enumerate(node[key]):
            _scan_references(child, f"{path}.{key}[{index}]", refs)
    elif key == "not":
        _scan_references(node["not"], f"{path}.not", refs)
    elif key == "ref" and isinstance(node["ref"], str) and node["ref"]:
        refs.append((node["ref"], f"{path}.ref"))


def _references(rules: dict[str, dict[str, Any]], name: str) -> list[str]:
    refs: list[tuple[str, str]] = []
    _scan_references(rules[name], name, refs)
    return sorted({target for target, _ in refs if target in rules})


def _find_cycles(rules: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Detect cycles in the rule reference graph (DFS), normalized & deduped."""
    cycles: set[tuple[str, ...]] = set()

    def dfs(current: str, stack: list[str], on_stack: set[str]) -> None:
        for nxt in _references(rules, current):
            if nxt in on_stack:
                core = stack[stack.index(nxt):]
                pivot = min(range(len(core)), key=lambda i: core[i])
                normalized = tuple(core[pivot:] + core[:pivot])
                cycles.add(normalized)
            else:
                stack.append(nxt)
                on_stack.add(nxt)
                dfs(nxt, stack, on_stack)
                on_stack.remove(nxt)
                stack.pop()

    for start in sorted(rules):
        dfs(start, [start], {start})

    return [list(cycle) + [cycle[0]] for cycle in sorted(cycles)]


def validate_rules(rules: dict[str, dict[str, Any]]) -> None:
    """Validate the full named rule set. Raises RuleError on any problem."""
    if not isinstance(rules, dict) or not rules:
        raise RuleError("invalid_rules", '"rules" must be a non-empty object of named rules')

    issues: list[dict[str, str]] = []
    for name in sorted(rules):
        if not isinstance(name, str) or not name:
            issues.append(_issue("<rule-name>", "rule names must be non-empty strings"))
            continue
        _validate_node(rules[name], name, issues)

    if issues:
        issues.sort(key=lambda item: (item["path"], item["message"]))
        raise RuleError(
            "invalid_rule_structure",
            f"{len(issues)} rule node(s) are malformed",
            issues,
        )

    # Reference resolution: every ref must point at an existing named rule.
    all_refs: list[tuple[str, str]] = []
    for name in sorted(rules):
        _scan_references(rules[name], name, all_refs)

    missing = sorted({(target, node_path) for target, node_path in all_refs if target not in rules})
    if missing:
        raise RuleError(
            "missing_reference",
            f"{len({target for target, _ in missing})} referenced rule(s) are not defined",
            [{"missing_rule": target, "at": node_path} for target, node_path in missing],
        )

    # Cycle detection over the resolved reference graph.
    cycles = _find_cycles(rules)
    if cycles:
        raise RuleError(
            "reference_cycle",
            f"{len(cycles)} reference cycle(s) detected between rules",
            [{"cycle": cycle} for cycle in cycles],
        )


# ---------------------------------------------------------------------------
# Ternary comparison helpers
# ---------------------------------------------------------------------------


def _negate(value: str) -> str:
    return {TRUE: FALSE, FALSE: TRUE, UNKNOWN: UNKNOWN}[value]


def _all_reduce(values: list[str]) -> str:
    # false dominates; unknown propagates if no false; otherwise true.
    if any(v == FALSE for v in values):
        return FALSE
    if any(v == UNKNOWN for v in values):
        return UNKNOWN
    return TRUE


def _any_reduce(values: list[str]) -> str:
    # true dominates; unknown propagates if no true; otherwise false.
    if any(v == TRUE for v in values):
        return TRUE
    if any(v == UNKNOWN for v in values):
        return UNKNOWN
    return FALSE


def _is_number(value: Any) -> bool:
    # Booleans are not treated as numbers for compliance comparisons.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json_equal(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    return left == right and type(left) is type(right)


def _orderable_pair(left: Any, right: Any) -> bool:
    """True only when an ordering comparison is meaningful (JSON-typed)."""
    if _is_number(left) and _is_number(right):
        return True
    return isinstance(left, str) and isinstance(right, str)


def _compare(op: str, fact_value: Any, literal: Any) -> str:
    if op in ("eq", "ne"):
        equal = _json_equal(fact_value, literal)
        if op == "eq":
            return TRUE if equal else FALSE
        return TRUE if not equal else FALSE

    if not _orderable_pair(fact_value, literal):
        return UNKNOWN

    if op == "gt":
        return TRUE if fact_value > literal else FALSE
    if op == "gte":
        return TRUE if fact_value >= literal else FALSE
    if op == "lt":
        return TRUE if fact_value < literal else FALSE
    return TRUE if fact_value <= literal else FALSE  # lte


def lookup_fact(facts: dict[str, Any], dotted_path: str) -> tuple[bool, Any]:
    """Resolve a dotted fact path.

    Returns ``(found, value)``. ``found`` is False as soon as a segment is
    missing; an explicit JSON null counts as a present value.
    """
    current: Any = facts
    for segment in dotted_path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return False, None
    return True, current


# ---------------------------------------------------------------------------
# Evaluation with complete, deterministic tracing
# ---------------------------------------------------------------------------


@dataclass
class Trace:
    used_facts: set[str] = field(default_factory=set)
    missing_fields: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)

    def as_dict(self, value: str) -> dict[str, Any]:
        return {
            "result": value,
            "used_facts": sorted(self.used_facts),
            "missing_fields": sorted(self.missing_fields),
            "hit_paths": sorted(self.paths),
        }


class _Evaluator:
    def __init__(self, rules: dict[str, dict[str, Any]], facts: dict[str, Any]):
        self.rules = rules
        self.facts = facts
        self.trace = Trace()
        # Value memoization per rule name (a DAG rule referenced twice is
        # computed once); body paths are re-applied to the trace on every
        # reference so each call site keeps a complete hit trace.
        self._cache: dict[str, str] = {}
        self._rule_paths: dict[str, set[str]] = {}
        self._rule_stack: list[str] = []

    def evaluate(self, target: str) -> str:
        if not isinstance(target, str) or not target:
            raise RuleError("invalid_target", '"target" must be a non-empty rule name')
        if target not in self.rules:
            raise RuleError(
                "unknown_target",
                f'target rule "{target}" is not defined',
                [{"target": target, "available": sorted(self.rules)}],
            )
        return self._eval_rule(target)

    def _record_path(self, path: str) -> None:
        self.trace.paths.add(path)
        if self._rule_stack:
            self._rule_paths[self._rule_stack[-1]].add(path)

    def _eval_rule(self, name: str) -> str:
        # Values are memoized per rule name (a DAG rule referenced twice
        # yields one value); body paths are re-applied on every reference
        # so each call site's hit trace stays complete. The rule is pushed
        # *before* expansion so its own body nodes are attributed to it.
        if name in self._cache:
            for path in self._rule_paths[name]:
                self.trace.paths.add(path)
            return self._cache[name]

        self._rule_stack.append(name)
        self._rule_paths[name] = set()
        self._cache[name] = self._eval_node(self.rules[name], name)
        self._rule_stack.pop()
        return self._cache[name]

    def _eval_node(self, node: dict[str, Any], path: str) -> str:
        """Evaluate the node located at structural ``path``.

        Each node records its own structural path; comparison leaves also
        record an ``<path>.<op>`` entry so the hit trace shows both where a
        comparison sits and which operator fired (e.g. ``r.all[0].gt``).
        """
        key = next(k for k in node if k in VALID_KEYS)
        self._record_path(path)

        if key == "all":
            values = [
                self._eval_node(child, f"{path}.all[{i}]")
                for i, child in enumerate(node["all"])
            ]
            return _all_reduce(values)

        if key == "any":
            values = [
                self._eval_node(child, f"{path}.any[{i}]")
                for i, child in enumerate(node["any"])
            ]
            return _any_reduce(values)

        if key == "not":
            return _negate(self._eval_node(node["not"], f"{path}.not"))

        if key == "ref":
            self._record_path(f"{path}.ref")
            return self._eval_rule(node["ref"])

        # Comparison leaf: structural path already recorded above; add the
        # operator-suffixed path for a self-explanatory hit trace.
        self._record_path(f"{path}.{key}")
        return self._eval_comparison(node)

    def _eval_comparison(self, node: dict[str, Any]) -> str:
        op = next(k for k in node if k in COMPARE_OPS)
        field_path, literal = node[op]
        found, value = lookup_fact(self.facts, field_path)
        if not found:
            self.trace.missing_fields.add(field_path)
            return UNKNOWN
        self.trace.used_facts.add(field_path)
        return _compare(op, value, literal)


def evaluate(
    rules: dict[str, dict[str, Any]],
    facts: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    """Validate references, then evaluate ``target`` with full tracing."""
    validate_rules(rules)
    if not isinstance(facts, dict):
        raise RuleError("invalid_facts", '"facts" must be an object')
    evaluator = _Evaluator(rules, facts)
    result = evaluator.evaluate(target)
    return evaluator.trace.as_dict(result)
