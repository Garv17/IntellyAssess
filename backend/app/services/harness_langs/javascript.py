"""JavaScript (Node) codec/driver for the function-signature judge.

Dynamically typed like Python — JSON.parse already produces native values in the
right shape, so there's no decode step at all.
"""

from __future__ import annotations

# Nothing to decode/encode — JSON.parse/JSON.stringify already do the whole job.
CODEC = ""


def boilerplate(function_name: str, return_type: str, parameters: list[dict]) -> str:
    params = ", ".join(p["name"] for p in parameters)
    return f"function {function_name}({params}) {{\n    \n}}\n"


def driver(function_name: str, return_type: str, parameters: list[dict]) -> str:
    return (
        "const __fs = require('fs');\n"
        "const __data = JSON.parse(__fs.readFileSync(0, 'utf8'));\n"
        f"const __result = {function_name}(...__data.params);\n"
        "console.log(JSON.stringify(__result));\n"
    )
