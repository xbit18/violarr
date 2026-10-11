import math
import re
from typing import Any, Iterable, Sequence

from release_language import should_force_english

PRESETS = {"unfiltered", "italian_preferred", "italian_only", "custom"}
TEXT_FIELDS = {"title": 0, "provider": 3}
NUMBER_FIELDS = {"size": 1, "seeders": 2}
TEXT_OPERATORS = {"contains", "not_contains", "equals"}
NUMBER_OPERATORS = {"equals", "gte", "lte"}
MAX_RULES = 100
MAX_TEXT_VALUE_LENGTH = 512
MAX_SCORE_MAGNITUDE = 1000


class ResultProcessingError(ValueError):
    pass


def validate_rules(rules: Any) -> list[dict[str, Any]]:
    if not isinstance(rules, list):
        raise ResultProcessingError("custom_rules must be a list")
    if len(rules) > MAX_RULES:
        raise ResultProcessingError(f"custom_rules cannot exceed {MAX_RULES} entries")

    validated = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ResultProcessingError(f"rule {index} must be an object")

        required = {"enabled", "field", "operator", "value", "action"}
        optional = {"score"}
        keys = set(rule)
        if not required.issubset(keys) or not keys.issubset(required | optional):
            raise ResultProcessingError(f"rule {index} has invalid fields")
        if not isinstance(rule["enabled"], bool):
            raise ResultProcessingError(f"rule {index} enabled must be a boolean")

        field = rule["field"]
        operator = rule["operator"]
        action = rule["action"]
        value = rule["value"]
        if field not in TEXT_FIELDS | NUMBER_FIELDS:
            raise ResultProcessingError(f"rule {index} has an invalid field")
        allowed_operators = TEXT_OPERATORS if field in TEXT_FIELDS else NUMBER_OPERATORS
        if operator not in allowed_operators:
            raise ResultProcessingError(f"rule {index} has an invalid operator")
        if action not in {"score", "exclude"}:
            raise ResultProcessingError(f"rule {index} has an invalid action")

        if field in TEXT_FIELDS:
            if not isinstance(value, str) or not value or len(value) > MAX_TEXT_VALUE_LENGTH:
                raise ResultProcessingError(f"rule {index} requires a bounded text value")
        elif not _is_finite_number(value):
            raise ResultProcessingError(f"rule {index} requires a finite numeric value")

        if action == "score":
            if "score" not in rule or not _is_finite_number(rule["score"]):
                raise ResultProcessingError(f"rule {index} score action requires a score")
            if abs(rule["score"]) > MAX_SCORE_MAGNITUDE:
                raise ResultProcessingError(f"rule {index} score is out of range")
        elif "score" in rule:
            raise ResultProcessingError(f"rule {index} exclude action cannot define a score")

        validated.append(dict(rule))
    return validated


def process_results(
    rows: Iterable[Sequence[Any]],
    preset: str,
    custom_rules: Any,
    *,
    subtitle_language_correction: bool = False,
    observer=None,
) -> list[Sequence[Any]]:
    if preset not in PRESETS:
        raise ResultProcessingError("unknown result-processing preset")

    materialized = list(rows)
    rules = validate_rules(custom_rules) if preset == "custom" else []
    excludes = [
        (i, rule) for i, rule in enumerate(rules) if rule["enabled"] and rule["action"] == "exclude"
    ]
    scores = [
        (i, rule) for i, rule in enumerate(rules) if rule["enabled"] and rule["action"] == "score"
    ]
    ranked = []
    for index, row in enumerate(materialized):
        reason = None
        score = 0
        matched_scores = []
        if preset == "italian_only":
            if not _has_explicit_italian_marker(_value(row, 0), subtitle_language_correction):
                reason = "missing_italian_marker"
        elif preset == "italian_preferred":
            score = _italian_score(row, subtitle_language_correction)
        elif preset == "custom":
            for rule_index, rule in excludes:
                if _matches(row, rule):
                    reason = f"custom_rule:{rule_index}"
                    break
            if reason is None:
                for rule_index, rule in scores:
                    if _matches(row, rule):
                        score += rule["score"]
                        if observer is not None:
                            matched_scores.append(rule_index)
        if reason is None:
            ranked.append((index, row, score))
        if observer is not None:
            observer(index, row, reason, score, matched_scores)
    ranked.sort(key=lambda entry: -entry[2])
    if observer is not None:
        observer.order = [entry[0] for entry in ranked]
    return [entry[1] for entry in ranked]


def _stable_rank(rows, score):
    return [row for _, row in sorted(enumerate(rows), key=lambda item: -score(item[1]))]


def _italian_score(row, subtitle_language_correction=False):
    title = _value(row, 0)
    if _has_explicit_italian_marker(title, subtitle_language_correction):
        return 100
    tokens = _title_tokens(title)
    if "MULTI" in tokens or "DUAL" in tokens:
        return 25
    return 0


def _has_explicit_italian_marker(title, subtitle_language_correction=False):
    if subtitle_language_correction and should_force_english(title):
        return False
    return bool(_title_tokens(title) & {"ITA", "ITALIAN", "ITALIANO"})


def _title_tokens(title):
    if not isinstance(title, str):
        return set()
    return set(re.findall(r"[A-Z0-9]+", title.upper()))


def _matches(row, rule):
    field = rule["field"]
    actual = _value(row, (TEXT_FIELDS | NUMBER_FIELDS)[field])
    expected = rule["value"]
    operator = rule["operator"]

    if field in TEXT_FIELDS:
        if actual is None:
            return operator == "not_contains"
        actual_text = str(actual).casefold()
        expected_text = expected.casefold()
        if operator == "contains":
            return expected_text in actual_text
        if operator == "not_contains":
            return expected_text not in actual_text
        return actual_text == expected_text

    if not _is_finite_number(actual):
        return False
    if operator == "equals":
        return actual == expected
    if operator == "gte":
        return actual >= expected
    return actual <= expected


def _value(row, index):
    try:
        return row[index]
    except (IndexError, TypeError):
        return None


def _is_finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
