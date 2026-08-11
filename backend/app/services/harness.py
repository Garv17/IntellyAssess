"""Function-signature judge: assembles a runnable program from a static per-language
codec, the student's function, and a generated driver — then hands it to
`app.services.sandbox.execute` exactly like a classic stdin/stdout submission.

Deliberately not touching sandbox.py: it stays a dumb "compile this source, run it
against these stdin strings" executor. Only what gets built as "source" and how the
result gets graded differ between the two judge modes.
"""

from __future__ import annotations

import json
import re

from app.services.harness_langs import c as _c
from app.services.harness_langs import cpp as _cpp
from app.services.harness_langs import java as _java
from app.services.harness_langs import javascript as _javascript
from app.services.harness_langs import python as _python

# The closed set of parameter/return types this judge understands. Adding a type
# means adding one entry here plus one decode/encode pair in every harness_langs
# module below — nothing else in the judge pipeline needs to change.
PARAM_TYPES: frozenset[str] = frozenset(
    {
        "int",
        "float",
        "bool",
        "string",
        "char",
        "int[]",
        "float[]",
        "bool[]",
        "string[]",
        "int[][]",
    }
)

# Types compared with a float epsilon rather than exact equality.
_FLOAT_TYPES = {"float", "float[]"}

_LANG_MODULES = {
    "python": _python,
    "javascript": _javascript,
    "java": _java,
    "cpp": _cpp,
    "c": _c,
}

# Adding a language means adding one new module here implementing the same
# boilerplate()/driver()/CODEC interface — nothing else needs to change either.
SUPPORTED_FUNCTION_LANGUAGES: frozenset[str] = frozenset(_LANG_MODULES.keys())


def _lang_module(language: str):
    try:
        return _LANG_MODULES[language]
    except KeyError:
        raise ValueError(f"Function-signature mode does not support language: {language}") from None


def generate_boilerplate(
    language: str, function_name: str, return_type: str, parameters: list[dict]
) -> str:
    return _lang_module(language).boilerplate(function_name, return_type, parameters)


def build_program(
    language: str,
    function_name: str,
    return_type: str,
    parameters: list[dict],
    student_code: str,
) -> str:
    """Concatenates codec + student's function + generated driver into one source
    file, exactly what gets passed to sandbox.execute as "code"."""
    mod = _lang_module(language)
    code = student_code.strip()
    if language == "java":
        # Only one class in the assembled file may be `public` (the driver's Main,
        # below) — guard against a student typing `public class Solution` too,
        # which would otherwise fail to compile against the fixed Main.java filename
        # sandbox.py's Java run_cmd expects.
        code = re.sub(r"\bpublic(\s+)class(\s+)Solution\b", r"class\2Solution", code)
    driver_code = mod.driver(function_name, return_type, parameters)
    return "\n\n".join(part for part in (mod.CODEC.strip(), code, driver_code.strip()) if part)


def encode_stdin(param_values: list) -> str:
    return json.dumps({"params": param_values})


def _values_equal(expected: object, actual: object, type_name: str) -> bool:
    if type_name == "float":
        try:
            return abs(float(expected) - float(actual)) < 1e-6  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
    if type_name == "float[]":
        if (
            not isinstance(expected, list)
            or not isinstance(actual, list)
            or len(expected) != len(actual)
        ):
            return False
        return all(_values_equal(e, a, "float") for e, a in zip(expected, actual, strict=False))
    return expected == actual


def compare(return_type: str, actual_stdout: str, expected_value: object) -> tuple[bool, str | None]:
    """Returns (matches, parse_error). A parse_error means the program's stdout
    wasn't valid JSON at all — a runtime/driver-level failure, distinct from the
    student's function simply returning the wrong value."""
    try:
        actual_value = json.loads(actual_stdout.strip())
    except (json.JSONDecodeError, ValueError) as exc:
        return False, f"Could not parse program output as JSON: {exc}"
    return _values_equal(expected_value, actual_value, return_type), None
