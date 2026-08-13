"""Function-signature boilerplate: the starter code a student sees when a coding
question declares a function signature instead of a stdin/stdout program.

This build is submission-only — nothing here assembles or runs a program. The
driver/codec templates still present in the harness_langs modules are unused on
this branch; only `boilerplate()` is called, from the admin exam builder.
"""

from __future__ import annotations

from app.services.harness_langs import c as _c
from app.services.harness_langs import cpp as _cpp
from app.services.harness_langs import java as _java
from app.services.harness_langs import javascript as _javascript
from app.services.harness_langs import python as _python

# The closed set of parameter/return types a function-signature question may use.
# Adding a type means adding one entry here plus its rendering in every
# harness_langs module below.
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

_LANG_MODULES = {
    "python": _python,
    "javascript": _javascript,
    "java": _java,
    "cpp": _cpp,
    "c": _c,
}

# Adding a language means adding one new module here exposing boilerplate().
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
