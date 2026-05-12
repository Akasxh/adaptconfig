"""JSONPath subset extractor + template substitution / inject for the chain runtime.

These three primitives (``extract_jsonpath``, ``substitute_template``,
``apply_inject``) compose into the chain's value-flow:

    response  --extract-->  context  --template-->  next request

They're tested independently here so a regression in any one of them is
pin-pointable.
"""

from __future__ import annotations

import pytest

from finspark.services.chain import (
    ChainExecutionError,
    apply_inject,
    extract_jsonpath,
    substitute_template,
)

# ---------------------------------------------------------------------------
# extract_jsonpath
# ---------------------------------------------------------------------------


class TestExtractSimplePaths:
    def test_top_level_field(self) -> None:
        resp = {"access_token": "tok_xyz", "expires_in": 3600}
        rules = {"token": "$.access_token"}
        assert extract_jsonpath(resp, rules) == {"token": "tok_xyz"}

    def test_top_level_field_without_dollar_prefix(self) -> None:
        # Robustness: accept "$.field" or ".field" or "field"
        resp = {"access_token": "tok_xyz"}
        assert extract_jsonpath(resp, {"a": "access_token"}) == {"a": "tok_xyz"}

    def test_returns_scalar_int(self) -> None:
        resp = {"count": 7}
        assert extract_jsonpath(resp, {"n": "$.count"}) == {"n": 7}

    def test_returns_full_dict_when_path_resolves_to_object(self) -> None:
        resp = {"meta": {"version": "v1", "trace_id": "abc"}}
        assert extract_jsonpath(resp, {"m": "$.meta"}) == {
            "m": {"version": "v1", "trace_id": "abc"}
        }

    def test_multiple_rules_in_one_call(self) -> None:
        resp = {"a": 1, "b": 2, "c": 3}
        rules = {"first": "$.a", "second": "$.b"}
        assert extract_jsonpath(resp, rules) == {"first": 1, "second": 2}


class TestExtractNestedPaths:
    def test_two_levels_deep(self) -> None:
        resp = {"data": {"access_token": "abc123", "scope": "read"}}
        assert extract_jsonpath(resp, {"t": "$.data.access_token"}) == {"t": "abc123"}

    def test_three_levels_deep(self) -> None:
        resp = {"data": {"oauth": {"token": "deep_token"}}}
        assert extract_jsonpath(resp, {"t": "$.data.oauth.token"}) == {"t": "deep_token"}

    def test_array_index_zero(self) -> None:
        resp = {"items": [{"id": "first"}, {"id": "second"}]}
        assert extract_jsonpath(resp, {"id": "$.items[0].id"}) == {"id": "first"}

    def test_array_index_positive(self) -> None:
        resp = {"items": [{"id": "first"}, {"id": "second"}, {"id": "third"}]}
        assert extract_jsonpath(resp, {"id": "$.items[2].id"}) == {"id": "third"}

    def test_top_level_array_index(self) -> None:
        # $.users[0].name on payload  {"users": [{"name": "Alice"}]}
        resp = {"users": [{"name": "Alice"}, {"name": "Bob"}]}
        assert extract_jsonpath(resp, {"who": "$.users[0].name"}) == {"who": "Alice"}


class TestExtractMissingPaths:
    """Missing paths are silently dropped -- callers that need strict
    failure should validate the keys returned vs the keys requested."""

    def test_missing_top_level_field_silent(self) -> None:
        resp = {"data": {"x": 1}}
        rules = {"missing": "$.absent"}
        assert extract_jsonpath(resp, rules) == {}

    def test_missing_nested_field_silent(self) -> None:
        resp = {"data": {"x": 1}}
        rules = {"missing": "$.data.nonexistent"}
        assert extract_jsonpath(resp, rules) == {}

    def test_traversal_through_non_dict_silent(self) -> None:
        resp = {"data": "literal_string"}
        rules = {"missing": "$.data.field"}
        assert extract_jsonpath(resp, rules) == {}

    def test_array_out_of_range_silent(self) -> None:
        resp = {"items": [{"id": "only"}]}
        rules = {"oob": "$.items[5].id"}
        assert extract_jsonpath(resp, rules) == {}

    def test_mixed_present_and_missing(self) -> None:
        resp = {"a": 1}
        rules = {"present": "$.a", "absent": "$.b"}
        assert extract_jsonpath(resp, rules) == {"present": 1}

    def test_empty_path_skipped_with_warning(self) -> None:
        resp = {"a": 1}
        # "$" with nothing after strips to empty -- skipped
        assert extract_jsonpath(resp, {"x": "$"}) == {}


class TestExtractSchemaErrors:
    def test_non_string_jsonpath_raises(self) -> None:
        resp = {"a": 1}
        with pytest.raises(ChainExecutionError, match="must be a string"):
            extract_jsonpath(resp, {"x": 42})  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# substitute_template
# ---------------------------------------------------------------------------


class TestSubstituteTemplate:
    def test_single_field(self) -> None:
        ctx = {"auth": {"token": "tok123"}}
        assert substitute_template("{{auth.token}}", ctx) == "tok123"

    def test_with_surrounding_text(self) -> None:
        ctx = {"auth": {"token": "tok123"}}
        assert substitute_template("Bearer {{auth.token}}", ctx) == "Bearer tok123"

    def test_multiple_substitutions(self) -> None:
        ctx = {"auth": {"token": "t1"}, "user": {"id": "u1"}}
        out = substitute_template("{{auth.token}}-{{user.id}}", ctx)
        assert out == "t1-u1"

    def test_nested_field_two_levels(self) -> None:
        ctx = {"a": {"data": {"access_token": "deep"}}}
        assert substitute_template("{{a.data.access_token}}", ctx) == "deep"

    def test_no_template_tokens_returns_as_is(self) -> None:
        assert substitute_template("plain string", {}) == "plain string"

    def test_unknown_step_raises(self) -> None:
        with pytest.raises(ChainExecutionError, match="unknown step"):
            substitute_template("{{ghost.field}}", {"auth": {"token": "t"}})

    def test_unknown_field_raises(self) -> None:
        with pytest.raises(ChainExecutionError, match="not found"):
            substitute_template("{{auth.absent}}", {"auth": {"token": "t"}})

    def test_int_value_is_stringified(self) -> None:
        ctx = {"step": {"count": 42}}
        assert substitute_template("{{step.count}}", ctx) == "42"


# ---------------------------------------------------------------------------
# apply_inject
# ---------------------------------------------------------------------------


class TestApplyInject:
    def test_inject_into_header(self) -> None:
        tpl: dict = {"headers": {}, "body": {}}
        rules = {"headers.Authorization": "Bearer {{auth.token}}"}
        ctx = {"auth": {"token": "abc123"}}
        result = apply_inject(tpl, rules, ctx)
        assert result["headers"]["Authorization"] == "Bearer abc123"

    def test_inject_does_not_mutate_template(self) -> None:
        tpl: dict = {"headers": {}, "body": {}}
        rules = {"headers.X-Token": "{{a.t}}"}
        ctx = {"a": {"t": "v"}}
        apply_inject(tpl, rules, ctx)
        assert tpl["headers"] == {}
        assert tpl["body"] == {}

    def test_inject_nested_body_path(self) -> None:
        tpl: dict = {"body": {"payment": {}}}
        rules = {"body.payment.parent_txn_id": "{{initiate.txn_id}}"}
        ctx = {"initiate": {"txn_id": "TXN-001"}}
        result = apply_inject(tpl, rules, ctx)
        assert result["body"]["payment"]["parent_txn_id"] == "TXN-001"

    def test_inject_creates_missing_intermediate_dicts(self) -> None:
        tpl: dict = {}
        rules = {"body.user.id": "{{step.id}}"}
        ctx = {"step": {"id": "u-1"}}
        result = apply_inject(tpl, rules, ctx)
        assert result["body"]["user"]["id"] == "u-1"

    def test_inject_multiple_rules(self) -> None:
        tpl: dict = {"headers": {}, "body": {}}
        rules = {
            "headers.Authorization": "Bearer {{auth.token}}",
            "body.user_id": "{{user.id}}",
        }
        ctx = {"auth": {"token": "tok"}, "user": {"id": "u-7"}}
        result = apply_inject(tpl, rules, ctx)
        assert result["headers"]["Authorization"] == "Bearer tok"
        assert result["body"]["user_id"] == "u-7"

    def test_inject_overwrites_existing_value_at_target(self) -> None:
        tpl: dict = {"headers": {"Authorization": "Bearer placeholder"}}
        rules = {"headers.Authorization": "Bearer {{auth.token}}"}
        ctx = {"auth": {"token": "real"}}
        result = apply_inject(tpl, rules, ctx)
        assert result["headers"]["Authorization"] == "Bearer real"

    def test_empty_inject_path_raises(self) -> None:
        with pytest.raises(ChainExecutionError, match="empty target"):
            apply_inject({}, {"": "{{a.b}}"}, {"a": {"b": "v"}})

    def test_inject_through_non_dict_intermediate_raises(self) -> None:
        # body is a string, but inject tries to walk through it
        tpl: dict = {"body": "literal_string"}
        rules = {"body.field": "{{a.b}}"}
        # The implementation auto-overwrites non-dict intermediates with a
        # new dict so the inject can succeed -- this matches the
        # auto-create semantics for missing keys.
        ctx = {"a": {"b": "v"}}
        result = apply_inject(tpl, rules, ctx)
        assert result["body"]["field"] == "v"
