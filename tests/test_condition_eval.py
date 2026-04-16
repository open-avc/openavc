"""Tests for condition evaluation operators."""

import pytest

from server.core.condition_eval import eval_operator


# ---------------------------------------------------------------------------
# eq / ne
# ---------------------------------------------------------------------------

class TestEq:
    def test_equal_strings(self):
        assert eval_operator("eq", "on", "on") is True

    def test_not_equal_strings(self):
        assert eval_operator("eq", "on", "off") is False

    def test_equal_numbers(self):
        assert eval_operator("eq", 42, 42) is True

    def test_int_vs_float(self):
        assert eval_operator("eq", 1, 1.0) is True

    def test_equal_booleans(self):
        assert eval_operator("eq", True, True) is True

    def test_none_eq_none(self):
        assert eval_operator("eq", None, None) is True

    def test_none_eq_value(self):
        assert eval_operator("eq", None, "on") is False

    def test_value_eq_none(self):
        assert eval_operator("eq", "on", None) is False

    def test_empty_string_eq(self):
        assert eval_operator("eq", "", "") is True
        assert eval_operator("eq", "", "x") is False

    def test_zero_eq(self):
        assert eval_operator("eq", 0, 0) is True
        assert eval_operator("eq", 0, False) is True  # Python truthiness

    def test_alias_equals(self):
        assert eval_operator("equals", "on", "on") is True

    def test_alias_double_equals(self):
        assert eval_operator("==", 1, 1) is True

    def test_alias_equal(self):
        assert eval_operator("equal", "a", "a") is True


class TestNe:
    def test_not_equal(self):
        assert eval_operator("ne", "on", "off") is True

    def test_equal_returns_false(self):
        assert eval_operator("ne", "on", "on") is False

    def test_none_ne_value(self):
        assert eval_operator("ne", None, "x") is True

    def test_alias_not_equals(self):
        assert eval_operator("not_equals", 1, 2) is True

    def test_alias_bang_equals(self):
        assert eval_operator("!=", "a", "b") is True

    def test_alias_not_equal(self):
        assert eval_operator("not_equal", True, False) is True


# ---------------------------------------------------------------------------
# gt / lt / gte / lte
# ---------------------------------------------------------------------------

class TestGt:
    def test_greater(self):
        assert eval_operator("gt", 10, 5) is True

    def test_equal_not_greater(self):
        assert eval_operator("gt", 5, 5) is False

    def test_less_not_greater(self):
        assert eval_operator("gt", 3, 5) is False

    def test_none_actual(self):
        assert eval_operator("gt", None, 5) is False

    def test_none_target(self):
        assert eval_operator("gt", 5, None) is False

    def test_both_none(self):
        assert eval_operator("gt", None, None) is False

    def test_float_comparison(self):
        assert eval_operator("gt", 3.14, 3.0) is True

    def test_string_comparison(self):
        assert eval_operator("gt", "b", "a") is True

    def test_alias_greater_than(self):
        assert eval_operator("greater_than", 10, 5) is True

    def test_alias_angle_bracket(self):
        assert eval_operator(">", 10, 5) is True


class TestLt:
    def test_less(self):
        assert eval_operator("lt", 3, 5) is True

    def test_equal_not_less(self):
        assert eval_operator("lt", 5, 5) is False

    def test_greater_not_less(self):
        assert eval_operator("lt", 10, 5) is False

    def test_none_actual(self):
        assert eval_operator("lt", None, 5) is False

    def test_none_target(self):
        assert eval_operator("lt", 5, None) is False

    def test_alias_less_than(self):
        assert eval_operator("less_than", 3, 5) is True

    def test_alias_angle_bracket(self):
        assert eval_operator("<", 3, 5) is True


class TestGte:
    def test_greater(self):
        assert eval_operator("gte", 10, 5) is True

    def test_equal(self):
        assert eval_operator("gte", 5, 5) is True

    def test_less(self):
        assert eval_operator("gte", 3, 5) is False

    def test_none_returns_false(self):
        assert eval_operator("gte", None, 5) is False

    def test_alias_greater_or_equal(self):
        assert eval_operator("greater_or_equal", 5, 5) is True

    def test_alias_gte_symbol(self):
        assert eval_operator(">=", 5, 5) is True


class TestLte:
    def test_less(self):
        assert eval_operator("lte", 3, 5) is True

    def test_equal(self):
        assert eval_operator("lte", 5, 5) is True

    def test_greater(self):
        assert eval_operator("lte", 10, 5) is False

    def test_none_returns_false(self):
        assert eval_operator("lte", 5, None) is False

    def test_alias_less_or_equal(self):
        assert eval_operator("less_or_equal", 5, 5) is True

    def test_alias_lte_symbol(self):
        assert eval_operator("<=", 5, 5) is True


# ---------------------------------------------------------------------------
# truthy / falsy
# ---------------------------------------------------------------------------

class TestTruthy:
    def test_true(self):
        assert eval_operator("truthy", True, None) is True

    def test_false(self):
        assert eval_operator("truthy", False, None) is False

    def test_nonzero_number(self):
        assert eval_operator("truthy", 42, None) is True

    def test_zero(self):
        assert eval_operator("truthy", 0, None) is False

    def test_nonempty_string(self):
        assert eval_operator("truthy", "on", None) is True

    def test_empty_string(self):
        assert eval_operator("truthy", "", None) is False

    def test_none(self):
        assert eval_operator("truthy", None, None) is False

    def test_nonempty_list(self):
        assert eval_operator("truthy", [1], None) is True

    def test_empty_list(self):
        assert eval_operator("truthy", [], None) is False

    def test_target_ignored(self):
        # Target value is irrelevant for truthy
        assert eval_operator("truthy", True, "anything") is True
        assert eval_operator("truthy", False, "anything") is False


class TestFalsy:
    def test_false(self):
        assert eval_operator("falsy", False, None) is True

    def test_true(self):
        assert eval_operator("falsy", True, None) is False

    def test_zero(self):
        assert eval_operator("falsy", 0, None) is True

    def test_nonzero(self):
        assert eval_operator("falsy", 1, None) is False

    def test_empty_string(self):
        assert eval_operator("falsy", "", None) is True

    def test_none(self):
        assert eval_operator("falsy", None, None) is True

    def test_target_ignored(self):
        assert eval_operator("falsy", None, "anything") is True
        assert eval_operator("falsy", "x", "anything") is False


# ---------------------------------------------------------------------------
# Unknown operator
# ---------------------------------------------------------------------------

class TestUnknownOperator:
    def test_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown condition operator"):
            eval_operator("contains", "hello world", "hello")

    def test_error_message_includes_operator(self):
        with pytest.raises(ValueError, match="'bogus'"):
            eval_operator("bogus", 1, 2)


# ---------------------------------------------------------------------------
# Type coercion edge cases
# ---------------------------------------------------------------------------

class TestTypeMismatch:
    def test_string_number_eq(self):
        # Python "5" != 5
        assert eval_operator("eq", "5", 5) is False

    def test_bool_int_eq(self):
        # Python True == 1
        assert eval_operator("eq", True, 1) is True

    def test_string_gt_number_raises(self):
        # Python 3 doesn't compare str > int
        with pytest.raises(TypeError):
            eval_operator("gt", "abc", 5)
