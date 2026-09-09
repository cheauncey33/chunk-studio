"""Deterministic numeric helpers for standard-value audit.

This module is not a second production judge. Agent verdicts are authoritative
for match/mismatch. Use ``derive`` for unit conversion, comparators, gold
derivation, and offline regression — never to overwrite an agent verdict.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
import re
from typing import Any

from app.audit_semantics import (
    bound_tightness,
    comparator_of,
    extract_requirement_claim,
    normalize_unit_pair,
    numbers_in,
    operator_direction,
    plain_text,
)


CALIBER_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "caliber"
DEFAULT_CALIBER_PATH = CALIBER_DIR / "caliber_v1.json"

# Standard designations carry digits that must never be read as claimed values.
_STANDARD_NO_RE = re.compile(
    r"[A-Z]{2,}(?:/[A-Z]+)?\s*\d+(?:\.\d+)*(?:\s*[-—]\s*\d{4})?",
    re.IGNORECASE,
)
_TOTAL_LOSS_RE = re.compile(r"总\s*损\s*耗|总\s*损\s*失|total\s*loss", re.IGNORECASE)
_CATEGORICAL_RE = re.compile(r"^[A-Za-z0-9_.+\-/]+$")
_NUM = r"[-+]?\d+(?:\.\d+)?"

_TWO_SIDED_RE = re.compile(
    rf"(?P<low>{_NUM})\s*(?:≤|<)\s*[^\d≤<≥>]{{0,16}}?\s*(?:≤|<)\s*(?P<high>{_NUM})"
)
_RELATIVE_SYMMETRIC_RE = re.compile(
    rf"(?P<base>{_NUM})\s*\(\s*1\s*±\s*(?P<pct>{_NUM})\s*%\s*\)"
)
_RELATIVE_ONE_SIDED_RE = re.compile(
    rf"(?P<base>{_NUM})\s*\((?:[^()]*?)(?P<sign>[+\-])\s*(?P<pct>{_NUM})\s*%[^()]*\)"
)
_ABSOLUTE_SYMMETRIC_PCT_RE = re.compile(rf"(?P<base>{_NUM})\s*±\s*(?P<pct>{_NUM})\s*%")
_ABSOLUTE_SYMMETRIC_RE = re.compile(rf"(?P<base>{_NUM})\s*±\s*(?P<delta>{_NUM})")
_RANGE_RE = re.compile(
    rf"(?P<low>{_NUM})\s*[A-Za-zμΩ%°]{{0,4}}\s*(?:~|至|到)\s*(?P<high>{_NUM})"
)

_MAGNITUDE_RATIO = 10.0
_INTERVAL_MARKERS = ("±", "~", "至", "到", "允许偏差", "公差", "容差")

_PLACEHOLDER_VALUES = {"", "/", "-", "—", "–", "无", "n/a", "na", "／"}
_UNIT_FOLD = {
    "℃": "c",
    "°c": "c",
    "㎡": "m2",
    "ω": "ohm",
    "μ": "u",
    "µ": "u",
}

_CONFLICTING_BASES_MARKER = "conflicting_table_bindings"
_NO_TABLE_CLAIM_MARKER = "no_authoritative_table_claim"
_WORKFLOW_ERROR_MARKER = "workflow_error"

_DETERMINISTIC_DISCOVERY = {"deterministic_comparison", "deterministic_table_binding_cell"}
_BASIS_CHECK_FLAGS = {
    "edition_mismatch": "basis_edition_mismatch",
    "declared_standard_not_in_kb": "basis_incomplete",
}


@lru_cache(maxsize=4)
def load_caliber(path: str | None = None) -> dict[str, Any]:
    """Load one caliber definition; the active version is the default."""
    target = Path(path) if path else DEFAULT_CALIBER_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def legacy_status(verdict: str | None, caliber: dict[str, Any] | None = None) -> str | None:
    """Map one verdict onto the legacy four-value status vocabulary."""
    if verdict is None:
        return None
    table = (caliber or load_caliber()).get("crosswalk", {}).get("verdict_to_legacy_status", {})
    return table.get(verdict)


def _display_tolerance(target: float) -> float:
    return max(1e-6, abs(target) * 1e-4)


def _close(left: float, right: float) -> bool:
    if math.isinf(left) or math.isinf(right):
        return left == right
    return abs(left - right) <= _display_tolerance(right)


def _normalize_expression(raw: Any) -> str:
    text = plain_text(raw)
    text = text.translate(
        str.maketrans(
            {
                "（": "(",
                "）": ")",
                "％": "%",
                "＋": "+",
                "－": "-",
                "−": "-",
                "﹣": "-",
                "～": "~",
                "，": ",",
                "≦": "≤",
                "≧": "≥",
            }
        )
    )
    return text.replace("<=", "≤").replace(">=", "≥")


def _strip_standard_nos(text: str) -> str:
    return _STANDARD_NO_RE.sub(" ", text)


def canonical_unit(unit: Any) -> str | None:
    """Fold one unit label so that display variants stop blocking conversion.

    ``℃`` / ``°C`` and ``dB(A)`` / ``dB（A）`` are the same unit; refusing to
    convert them turns a comparable pair into applicability_undetermined.
    """
    text = _normalize_expression(unit).lower().replace(" ", "")
    text = re.sub(r"[\[\](){}]", "", text)
    for source, target in _UNIT_FOLD.items():
        text = text.replace(source, target)
    return text or None


def _comparable_text(value: Any) -> str:
    """Fold one claimed value for verbatim comparison (C-12 normalization)."""
    return _normalize_expression(value).lower().replace(" ", "").strip("：:，,。;")


def parse_interval(raw: Any, *, operator: str | None = None) -> dict[str, Any] | None:
    """Parse one claimed value into a closed interval, or refuse.

    Returns ``None`` when the text carries no recoverable numeric bound.  The
    ``form`` field distinguishes genuine range/tolerance expressions from plain
    bounds, which is what :func:`derive` uses to pick the C-08 path.
    """
    text = _normalize_expression(raw)
    if not text:
        return None
    stripped = _strip_standard_nos(text)

    two_sided = _TWO_SIDED_RE.search(stripped)
    if two_sided:
        low, high = float(two_sided.group("low")), float(two_sided.group("high"))
        return _interval(min(low, high), max(low, high), "two_sided_comparator", text)

    relative = _RELATIVE_SYMMETRIC_RE.search(stripped)
    if relative:
        base, pct = float(relative.group("base")), float(relative.group("pct"))
        span = abs(base) * pct / 100.0
        return _interval(base - span, base + span, "relative_symmetric", text, center=base)

    one_sided = _RELATIVE_ONE_SIDED_RE.search(stripped)
    if one_sided:
        base, pct = float(one_sided.group("base")), float(one_sided.group("pct"))
        span = abs(base) * pct / 100.0
        bound = comparator_of(text)
        if one_sided.group("sign") == "+":
            limit = base + span
            low = None if bound in {"le", "lt"} else min(base, limit)
            return _interval(low, limit, "relative_one_sided", text, center=base)
        limit = base - span
        high = None if bound in {"ge", "gt"} else max(base, limit)
        return _interval(limit, high, "relative_one_sided", text, center=base)

    percent = _ABSOLUTE_SYMMETRIC_PCT_RE.search(stripped)
    if percent:
        base, pct = float(percent.group("base")), float(percent.group("pct"))
        span = abs(base) * pct / 100.0
        return _interval(base - span, base + span, "relative_symmetric", text, center=base)

    absolute = _ABSOLUTE_SYMMETRIC_RE.search(stripped)
    if absolute:
        base, delta = float(absolute.group("base")), abs(float(absolute.group("delta")))
        return _interval(base - delta, base + delta, "absolute_symmetric", text, center=base)

    span_match = _RANGE_RE.search(stripped)
    if span_match:
        low, high = float(span_match.group("low")), float(span_match.group("high"))
        return _interval(min(low, high), max(low, high), "range", text)

    values = {round(number, 9) for number in numbers_in(stripped)}
    if len(values) != 1:
        return None
    bound = operator or comparator_of(text)
    value = values.pop()
    if bound in {"le", "lt"}:
        return _interval(None, value, "bound", text)
    if bound in {"ge", "gt"}:
        return _interval(value, None, "bound", text)
    return _interval(value, value, "scalar", text, center=value)


def _interval(
    low: float | None,
    high: float | None,
    form: str,
    source: str,
    *,
    center: float | None = None,
) -> dict[str, Any]:
    return {"low": low, "high": high, "form": form, "center": center, "source": source}


def is_range_form(interval: dict[str, Any] | None) -> bool:
    """True when the interval came from a range or tolerance expression."""
    return bool(interval) and interval["form"] not in {"bound", "scalar"}


def interval_relation(left: dict[str, Any], right: dict[str, Any]) -> str:
    """Relate a reported interval to a standard interval."""
    low_left = -math.inf if left["low"] is None else float(left["low"])
    high_left = math.inf if left["high"] is None else float(left["high"])
    low_right = -math.inf if right["low"] is None else float(right["low"])
    high_right = math.inf if right["high"] is None else float(right["high"])
    if _close(low_left, low_right) and _close(high_left, high_right):
        return "equal"
    tolerance_low = 0.0 if math.isinf(low_right) else _display_tolerance(low_right)
    tolerance_high = 0.0 if math.isinf(high_right) else _display_tolerance(high_right)
    if low_left >= low_right - tolerance_low and high_left <= high_right + tolerance_high:
        return "subset"
    if high_left < low_right - tolerance_low or low_left > high_right + tolerance_high:
        return "disjoint"
    if low_left <= low_right + tolerance_low and high_left >= high_right - tolerance_high:
        return "superset"
    return "overlap"


def _magnitude_class(left: float, right: float) -> str:
    if _close(left, right):
        return "within_display_tolerance"
    if left == 0.0 or right == 0.0:
        return "other"
    ratio = abs(left) / abs(right)
    if ratio >= _MAGNITUDE_RATIO or ratio <= 1.0 / _MAGNITUDE_RATIO:
        return "magnitude"
    return "other"


def _midpoint(interval: dict[str, Any]) -> float | None:
    if interval.get("center") is not None:
        return float(interval["center"])
    low, high = interval.get("low"), interval.get("high")
    if low is not None and high is not None:
        return (float(low) + float(high)) / 2.0
    if low is not None:
        return float(low)
    if high is not None:
        return float(high)
    return None


_TOLERANCE_RE = re.compile(rf"(?P<sign>[+\-±])?\s*(?P<size>{_NUM})\s*(?P<percent>%)?")


def _tolerance_interval(
    value: float,
    tolerance: Any,
    operator: str | None,
) -> dict[str, Any] | None:
    """Expand one recorded standard value by its recorded tolerance.

    Without this the tolerance is stored but ignored, and a report quoting the
    standard's own allowance looks looser than the standard.
    """
    text = _normalize_expression(tolerance)
    match = _TOLERANCE_RE.search(text) if text else None
    if match is None:
        return None
    size = abs(float(match.group("size")))
    span = abs(value) * size / 100.0 if match.group("percent") else size
    sign = match.group("sign") or "±"
    low = value - span if sign in {"-", "±"} else value
    high = value + span if sign in {"+", "±"} else value
    if operator in {"le", "lt"}:
        return _interval(None, high, "bound_with_tolerance", text, center=value)
    if operator in {"ge", "gt"}:
        return _interval(low, None, "bound_with_tolerance", text, center=value)
    return _interval(low, high, "absolute_symmetric", text, center=value)


def standard_claim(real: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize one ``real`` block into a comparable standard fact."""
    block = real if isinstance(real, dict) else {}
    raw = block.get("raw_value")
    numeric = block.get("numeric_value")
    tolerance = block.get("tolerance")
    reported_operator = str(block.get("operator") or "").strip() or None
    operator = None if reported_operator in {None, "unknown"} else reported_operator
    text = " ".join(part for part in (str(raw or ""), str(tolerance or "")) if part.strip())
    interval = parse_interval(text, operator=operator) if text.strip() else None
    if interval is None and isinstance(numeric, (int, float)):
        interval = parse_interval(str(numeric), operator=operator or "eq")
    if tolerance and isinstance(numeric, (int, float)) and not is_range_form(interval):
        interval = _tolerance_interval(float(numeric), tolerance, operator) or interval
    normalized_raw = str(raw or "").strip() or None
    return {
        "raw": normalized_raw,
        "numeric": float(numeric) if isinstance(numeric, (int, float)) else None,
        "unit": block.get("unit"),
        "operator": operator,
        "operator_declared": reported_operator,
        "tolerance": tolerance,
        "scope": block.get("scope"),
        "discovery_method": block.get("discovery_method"),
        "interval": interval,
        # A label such as "Dyn11" carries no bound, so it must not be read as a
        # missing standard value.
        "categorical": interval is None and numeric is None and bool(normalized_raw),
    }


def report_claim(
    reported_requirement: dict[str, Any] | None,
    *,
    project_name: str = "",
) -> dict[str, Any]:
    """Normalize one reported requirement into a comparable claim."""
    block = reported_requirement if isinstance(reported_requirement, dict) else {}
    text = str(block.get("text") or "")
    unit = block.get("unit")
    bundle = extract_requirement_claim(text, unit=unit, project_name=project_name)
    value = (bundle.get("claim") or {}).get("value") or {}
    property_text = ((bundle.get("claim") or {}).get("property") or {}).get("source_text") or ""
    raw = str(value.get("raw") or "")
    interval = parse_interval(raw or text, operator=str(value.get("operator") or "eq"))
    shape = _claim_shape(value, interval, raw or text)
    return {
        "text": text,
        "unit": value.get("unit") or unit,
        "operator": str(value.get("operator") or "eq"),
        "raw": raw,
        "kind": value.get("kind"),
        "property_text": property_text,
        "numbers": value.get("numbers") or [],
        "interval": interval,
        "shape": shape,
        "program_ready": bool(bundle.get("program_ready")),
        "reason_code": bundle.get("reason_code"),
    }


def _claim_shape(value: dict[str, Any], interval: dict[str, Any] | None, text: str) -> str:
    kind = str(value.get("kind") or "")
    placeholder = _comparable_text(value.get("raw"))
    if placeholder and placeholder in _PLACEHOLDER_VALUES:
        return "qualitative"
    if kind == "expression" and not is_range_form(interval):
        return "expression"
    if is_range_form(interval):
        return "interval"
    if kind in {"enum", "boolean"}:
        return "enum"
    meaningful = _strip_standard_nos(_normalize_expression(text))
    numbers = numbers_in(meaningful)
    if kind == "text" and not numbers:
        raw = plain_text(value.get("raw"))
        if raw and _CATEGORICAL_RE.match(raw):
            return "enum"
        return "qualitative"
    if not numbers:
        return "qualitative"
    # A restated value such as "0.3（构造值：0.3m）" is one claim, not two.
    if len({round(number, 9) for number in numbers}) == 1:
        return "scalar"
    if any(marker in meaningful for marker in _INTERVAL_MARKERS):
        return "interval"
    return "expression"


def _evidence_state(
    standard: dict[str, Any],
    markers: set[str],
    missing_context_fields: list[Any] | None,
) -> tuple[str, str | None]:
    if _CONFLICTING_BASES_MARKER in markers:
        return "conflicting_bases", _CONFLICTING_BASES_MARKER
    if missing_context_fields:
        return "applicability_missing", "missing_context_fields"
    if standard["categorical"]:
        return "found", "categorical_standard_value"
    has_value = standard["interval"] is not None or standard["numeric"] is not None
    if not has_value:
        if _NO_TABLE_CLAIM_MARKER in markers:
            return "not_found", _NO_TABLE_CLAIM_MARKER
        return "not_found", "no_standard_value_recorded"
    if standard["operator"] is None:
        return "applicability_missing", "standard_operator_unknown"
    return "found", None


def _scoreability(standard: dict[str, Any], review: dict[str, Any] | None) -> tuple[bool, str]:
    status = str((review or {}).get("status") or "")
    discovery = str(standard.get("discovery_method") or "")
    if discovery in _DETERMINISTIC_DISCOVERY:
        return True, discovery
    if status == "human_confirmed":
        return True, "human_confirmed"
    if discovery == "unverified_model_value":
        return False, "unverified_model_value"
    if discovery == "judgment_comparison":
        return False, "pending_confirmation"
    return False, "unknown_discovery_method"


def _comparison_envelope(
    report: dict[str, Any],
    standard: dict[str, Any],
    units: dict[str, Any],
) -> dict[str, Any]:
    return {
        "left_normalized": None,
        "right_normalized": None,
        "unit_state": units["state"],
        "relation": "not_comparable",
        "tightness": None,
        "claim_shape": report["shape"],
        "comparator_state": None,
        "interval_relation": None,
        "numeric_delta_class": None,
        "left_raw": report["raw"] or report["text"],
        "right_raw": standard["raw"],
        "left_unit": units["left_unit"],
        "right_unit": units["right_unit"],
    }


def _compare_scalar(
    report: dict[str, Any],
    standard: dict[str, Any],
    units: dict[str, Any],
) -> dict[str, Any]:
    comparison = _comparison_envelope(report, standard, units)
    left_interval, right_interval = report["interval"], standard["interval"]
    left_value = _midpoint(left_interval) if left_interval else None
    right_value = _midpoint(right_interval) if right_interval else standard["numeric"]
    if left_value is None or right_value is None:
        comparison["reason"] = "non_scalar_or_ambiguous"
        return comparison
    left_value *= float(units["scale_left"] or 1.0)
    right_value *= float(units["scale_right"] or 1.0)
    report_operator = report["operator"]
    standard_operator = standard["operator"] or "eq"
    if (
        report_operator in {"eq", "between"}
        and standard_operator in {"eq", "between", "tolerance", "range"}
        and left_value < 0 <= right_value
    ):
        # Polarity-only difference on a magnitude spec, e.g. -75 kV impulse.
        comparison["polarity_normalized"] = True
        left_value = abs(left_value)
    left_direction = operator_direction(report_operator)
    right_direction = operator_direction(standard_operator)
    if left_direction == "exact" and right_direction in {"upper", "lower"}:
        operator, comparator_state = standard_operator, "aligned"
    elif right_direction == "exact" and left_direction in {"upper", "lower"}:
        operator, comparator_state = report_operator, "aligned"
    elif left_direction and right_direction and left_direction != right_direction:
        comparison.update(
            left_normalized=left_value,
            right_normalized=right_value,
            relation="conflicts",
            comparator_state="flipped",
            numeric_delta_class=_magnitude_class(left_value, right_value),
        )
        return comparison
    else:
        operator = standard_operator
        comparator_state = "aligned" if left_direction == right_direction else "mixed"
    tightness = bound_tightness(left_value, right_value, operator)
    delta_class = _magnitude_class(left_value, right_value)
    if tightness in {"looser", "different"} and delta_class == "within_display_tolerance":
        tightness = "equal"
        comparison["display_tolerance_applied"] = True
    if operator in {"le", "lt", "ge", "gt"}:
        relation = "supports" if tightness in {"equal", "stricter"} else "conflicts"
    else:
        relation = "equal" if tightness == "equal" else "different"
    comparison.update(
        left_normalized=left_value,
        right_normalized=right_value,
        relation=relation,
        tightness=tightness,
        comparator_state=comparator_state,
        numeric_delta_class=delta_class,
        resolved_operator=operator,
    )
    return comparison


_BAND_FORMS = {"relative_symmetric", "absolute_symmetric"}
_MIRRORABLE_FORMS = _BAND_FORMS | {"range", "scalar"}


def _align_interval_polarity(
    left: dict[str, Any],
    right: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Mirror a negative reported band onto the unsigned standard band.

    A -75 kV impulse claim and a 75 kV table limit are one requirement; the sign
    is a polarity convention, not a content difference (C-08 polarity clause).
    """
    if left["form"] not in _MIRRORABLE_FORMS:
        return left, False
    high_left = math.inf if left["high"] is None else float(left["high"])
    low_right = -math.inf if right["low"] is None else float(right["low"])
    if high_left >= 0 or low_right < 0:
        return left, False
    mirrored = {
        **left,
        "low": None if left["high"] is None else -float(left["high"]),
        "high": None if left["low"] is None else -float(left["low"]),
        "center": None if left.get("center") is None else -float(left["center"]),
    }
    return mirrored, True


def _resolve_bound_direction(
    left: dict[str, Any],
    right: dict[str, Any],
    left_operator: str,
    right_operator: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Give a degenerate scalar side the bound direction of the other side.

    Standard facts often record only the limit number with ``operator: eq``;
    without this, ``[0.8, 0.8]`` and ``(-inf, 0.234]`` look disjoint (C-08
    comparator_direction_first).
    """
    left_direction = operator_direction(left_operator)
    right_direction = operator_direction(right_operator)
    pairs = (
        (right, left_direction, right_direction, False),
        (left, right_direction, left_direction, True),
    )
    for interval, borrowed, own, is_left in pairs:
        if interval["form"] != "scalar" or own != "exact" or borrowed not in {"upper", "lower"}:
            continue
        value = interval.get("center")
        if value is None:
            continue
        bounded = {
            **interval,
            "low": None if borrowed == "upper" else float(value),
            "high": float(value) if borrowed == "upper" else None,
            "form": "bound",
        }
        return (bounded, right, True) if is_left else (left, bounded, True)
    return left, right, False


def _compare_interval(
    report: dict[str, Any],
    standard: dict[str, Any],
    units: dict[str, Any],
) -> dict[str, Any]:
    comparison = _comparison_envelope(report, standard, units)
    comparison["applied_rules"] = ["C-08"]
    left_interval, right_interval = report["interval"], standard["interval"]
    if left_interval is None or right_interval is None:
        comparison["reason"] = (
            "left_interval_unparseable" if left_interval is None else "right_interval_unparseable"
        )
        return comparison
    left = _scaled(left_interval, float(units["scale_left"] or 1.0))
    right = _scaled(right_interval, float(units["scale_right"] or 1.0))
    reported_base = _midpoint(left)
    recorded_value = _midpoint(right)
    if (
        left_interval["form"].startswith(("relative", "absolute"))
        and right_interval["form"] in {"bound", "scalar"}
        and not standard["tolerance"]
        and reported_base is not None
        and recorded_value is not None
        and _close(float(reported_base), float(recorded_value))
    ):
        # The reported band expands exactly the recorded limit, so the caliber
        # cannot tell a quoted standard tolerance from a self-granted one.
        comparison["reason"] = "standard_tolerance_not_recorded"
        return comparison
    left, right, direction_resolved = _resolve_bound_direction(
        left,
        right,
        report["operator"],
        standard["operator"] or "eq",
    )
    if direction_resolved:
        comparison["bound_direction_resolved"] = True
    left, mirrored = _align_interval_polarity(left, right)
    if mirrored:
        comparison["polarity_normalized"] = True
    relation = interval_relation(left, right)
    center_left, center_right = left.get("center"), right.get("center")
    band_pair = (
        left_interval["form"] in _BAND_FORMS
        and right_interval["form"] in _BAND_FORMS
        and center_left is not None
        and center_right is not None
    )
    left_mid, right_mid = _midpoint(left), _midpoint(right)
    delta_class = (
        _magnitude_class(left_mid, right_mid)
        if left_mid is not None and right_mid is not None
        else None
    )
    comparison.update(
        left_normalized=[left["low"], left["high"]],
        right_normalized=[right["low"], right["high"]],
        comparator_state="aligned",
        interval_relation=relation,
        numeric_delta_class=delta_class,
        left_form=left_interval["form"],
        right_form=right_interval["form"],
    )
    if band_pair and not _close(float(center_left), float(center_right)):
        # C-08 center_first: the nominal is wrong, so band width does not decide.
        comparison.update(
            relation="different",
            tightness="different",
            center_relation="different",
            numeric_delta_class=_magnitude_class(float(center_left), float(center_right)),
        )
        return comparison
    comparison.update(
        relation={
            "equal": "equal",
            "subset": "supports",
            "superset": "conflicts",
            "overlap": "conflicts",
            "disjoint": "conflicts",
        }[relation],
        tightness={"equal": "equal", "subset": "stricter"}.get(relation, "looser"),
        band_decides=relation in {"superset", "overlap"},
    )
    if band_pair:
        comparison["center_relation"] = "equal"
    return comparison


def _scaled(interval: dict[str, Any], scale: float) -> dict[str, Any]:
    if scale == 1.0:
        return interval
    return {
        **interval,
        "low": None if interval["low"] is None else float(interval["low"]) * scale,
        "high": None if interval["high"] is None else float(interval["high"]) * scale,
        "center": None if interval.get("center") is None else float(interval["center"]) * scale,
    }


def _compare_categorical(
    report: dict[str, Any],
    standard: dict[str, Any],
    units: dict[str, Any],
) -> dict[str, Any]:
    comparison = _comparison_envelope(report, standard, units)
    comparison["applied_rules"] = ["C-11"]
    left = plain_text(report["raw"]).lower().replace(" ", "")
    right = plain_text(standard["raw"]).lower().replace(" ", "")
    if not left or not right:
        comparison["reason"] = "categorical_value_missing"
        return comparison
    comparison.update(left_normalized=left, right_normalized=right, comparator_state="aligned")
    if left == right:
        comparison.update(relation="equal", tightness="equal")
        return comparison
    if left in right or right in left:
        # "Dyn11" against a cell holding "Dyn11Yyn0": the extraction lost the
        # label boundaries, so membership is not decidable here (C-11 must_not).
        comparison["reason"] = "categorical_enumeration_ambiguous"
        return comparison
    comparison.update(relation="different", tightness=None)
    return comparison


def _mismatch_kind(comparison: dict[str, Any], report: dict[str, Any]) -> tuple[str, str]:
    """Pick one mismatch subtype under the caliber kind priority."""
    if comparison.get("comparator_state") == "flipped":
        return "comparator_flip", "deterministic"
    if report["shape"] == "enum":
        return "wrong_label", "deterministic"
    if comparison.get("numeric_delta_class") == "magnitude":
        return "magnitude_error", "deterministic"
    if comparison.get("band_decides"):
        return "bandwidth_exceeded", "deterministic"
    # wrong_level and wrong_condition rank above numeric_looser but need the
    # applicability semantics the comparison does not carry, so a numeric-only
    # derivation reports the numeric subtype and marks its own confidence.
    confidence = "numeric_only" if comparison.get("resolved_operator") == "eq" else "deterministic"
    return "numeric_looser", confidence


def derive(
    reported_requirement: dict[str, Any] | None,
    real: dict[str, Any] | None,
    conditions: list[Any] | None = None,
    caliber: dict[str, Any] | None = None,
    *,
    missing_context_fields: list[Any] | None = None,
    review: dict[str, Any] | None = None,
    project_name: str = "",
    basis_check: list[str] | None = None,
) -> dict[str, Any]:
    """Derive one verdict from a reported requirement and a standard fact.

    The result is only meaningful together with ``derivation.rules``; callers
    must persist it so a later caliber version can be diffed against it.
    """
    active = caliber or load_caliber()
    markers = {item for item in (conditions or []) if isinstance(item, str)}
    report = report_claim(reported_requirement, project_name=project_name)
    standard = standard_claim(real)
    scoreable, scoreable_reason = _scoreability(standard, review)
    flags: list[str] = []
    rules: list[str] = []
    notes: list[str] = []

    if basis_check:
        rules.append("C-07")
        flags.extend(
            _BASIS_CHECK_FLAGS[check] for check in basis_check if check in _BASIS_CHECK_FLAGS
        )

    def result(
        verdict: str | None,
        kind: str | None,
        comparison: dict[str, Any],
        *,
        underivable_reason: str | None = None,
        kind_confidence: str = "deterministic",
    ) -> dict[str, Any]:
        return {
            "caliber_version": active.get("caliber_version"),
            "verdict": verdict,
            "kind": kind,
            "flags": sorted(set(flags)),
            "scoreable": scoreable,
            "legacy_status": legacy_status(verdict, active),
            "underivable_reason": underivable_reason,
            "derivation": {
                "rules": rules,
                "comparison": comparison,
                "kind_confidence": kind_confidence,
                "scoreable_reason": scoreable_reason,
                "notes": notes,
            },
        }

    units = normalize_unit_pair(canonical_unit(report["unit"]), canonical_unit(standard["unit"]))
    envelope = _comparison_envelope(report, standard, units)
    claimed = _comparable_text(report["raw"] or report["text"])
    recorded = _comparable_text(standard["raw"])
    verbatim = bool(claimed) and claimed == recorded

    def verbatim_match() -> dict[str, Any]:
        rules.extend(["C-00", "C-12"])
        envelope.update(
            relation="equal",
            tightness="equal",
            comparator_state="aligned",
            left_normalized=claimed,
            right_normalized=recorded,
        )
        return result("match", "exact", envelope)

    if _WORKFLOW_ERROR_MARKER in markers:
        # A pipeline crash is not an adjudication outcome; refuse to invent one.
        envelope["reason"] = _WORKFLOW_ERROR_MARKER
        return result(None, None, envelope, underivable_reason=_WORKFLOW_ERROR_MARKER)

    if not report["text"].strip():
        # An absent requirement is missing input, not a qualitative clause.
        envelope["reason"] = "requirement_text_missing"
        return result(None, None, envelope, underivable_reason="requirement_text_missing")

    if report["shape"] == "qualitative":
        rules.append("C-05")
        envelope["reason"] = "qualitative_clause"
        return result("out_of_scope", None, envelope)

    evidence_state, evidence_reason = _evidence_state(standard, markers, missing_context_fields)
    envelope["evidence_state"] = evidence_state
    if evidence_reason:
        notes.append(evidence_reason)
    if evidence_state == "conflicting_bases":
        rules.append("C-06")
        if verbatim:
            notes.append("basis_conflict_immaterial")
            return verbatim_match()
        notes.append("standard_priority_order not resolvable from gold inputs")
        return result("unevaluable", "basis_conflict", envelope)
    if evidence_state == "applicability_missing":
        rules.append("C-09")
        return result("unevaluable", "applicability_undetermined", envelope)
    if evidence_state == "not_found":
        rules.append("C-10")
        notes.append("admission_gate not verifiable offline")
        return result("unevaluable", "standard_not_found", envelope)

    total_loss = bool(_TOTAL_LOSS_RE.search(report["property_text"] or report["text"]))
    envelope["property_family"] = "total_loss" if total_loss else "any"

    if verbatim and not total_loss:
        return verbatim_match()

    if units["state"] == "unavailable":
        rules.append("C-09")
        envelope["reason"] = "unit_conversion_unavailable"
        return result("unevaluable", "applicability_undetermined", envelope)

    if report["shape"] == "expression":
        rules.append("C-08")
        envelope["reason"] = "expression_unparseable"
        return result("unevaluable", "applicability_undetermined", envelope)

    if report["shape"] == "enum":
        comparison = _compare_categorical(report, standard, units)
    elif report["shape"] == "interval" or is_range_form(standard["interval"]):
        comparison = _compare_interval(report, standard, units)
    else:
        comparison = _compare_scalar(report, standard, units)
    comparison["evidence_state"] = evidence_state
    comparison["property_family"] = envelope["property_family"]
    rules.extend(comparison.get("applied_rules") or [])

    if comparison["relation"] == "not_comparable":
        if not (comparison.get("applied_rules") or []):
            rules.append("C-09")
        notes.append(str(comparison.get("reason") or "not_comparable"))
        return result("unevaluable", "applicability_undetermined", comparison)

    rules.append("C-00")

    if total_loss:
        rules.append("C-03")
        if "derived_sum_comparable" not in markers and standard["scope"] is None:
            notes.append("total_loss claim needs the no-load plus load-loss sum")
            return result("unevaluable", "applicability_undetermined", comparison)
        if report["operator"] not in {"le", "lt", "ge", "gt"}:
            # A reported aggregate verified against its addends needs equality.
            if comparison["relation"] == "equal" or comparison["tightness"] == "equal":
                return result("match", "formula_aggregate", comparison)
            return result("mismatch", "formula_aggregate", comparison)
        # A limit claim against the aggregate limit keeps ordinary bound
        # semantics, so self-tightening stays compliant (C-03 claim_role).
        notes.append("total_loss limit claim compared with bound semantics")

    if comparison["relation"] in {"equal", "supports"}:
        if comparison.get("interval_relation") == "subset":
            flags.append("tighter_than_standard")
            return result("match", "exact", comparison)
        if comparison["tightness"] == "stricter":
            rules.append("C-01")
            flags.append("tighter_than_standard")
            return result("match", "within_standard", comparison)
        if units["state"] == "converted":
            rules.append("C-02")
            return result("match", "unit_equivalent", comparison)
        if comparison.get("display_tolerance_applied"):
            rules.append("C-04")
        return result("match", "exact", comparison)

    kind, confidence = _mismatch_kind(comparison, report)
    rules.append("kind_priority")
    return result("mismatch", kind, comparison, kind_confidence=confidence)


def derive_case(case: dict[str, Any], *, caliber: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive one verdict from a gold case in the transformer report datasets."""
    project = case.get("detection_project") or {}
    return derive(
        project.get("reported_requirement"),
        case.get("real"),
        case.get("conditions"),
        caliber,
        missing_context_fields=case.get("missing_context_fields"),
        review=case.get("review"),
        project_name=str(project.get("project_name") or ""),
    )
