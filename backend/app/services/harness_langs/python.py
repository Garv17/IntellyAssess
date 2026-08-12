"""Python codec/driver for the function-signature judge.

Python is dynamically typed and json.loads already produces native
list/int/float/str/bool values in the right shape, so there's no decode step at
all — the driver just unpacks params positionally.
"""

from __future__ import annotations

_TYPE_HINTS = {
    "int": "int",
    "float": "float",
    "bool": "bool",
    "string": "str",
    "char": "str",
    "int[]": "list[int]",
    "float[]": "list[float]",
    "bool[]": "list[bool]",
    "string[]": "list[str]",
    "int[][]": "list[list[int]]",
}

# Nothing to decode/encode — json.loads/json.dumps already do the whole job.
CODEC = ""


def boilerplate(function_name: str, return_type: str, parameters: list[dict]) -> str:
    params = ", ".join(f"{p['name']}: {_TYPE_HINTS[p['type']]}" for p in parameters)
    ret = _TYPE_HINTS[return_type]
    return (
        "class Solution:\n"
        f"    def {function_name}(self, {params}) -> {ret}:\n"
        "        pass\n"
    )


def driver(function_name: str, return_type: str, parameters: list[dict]) -> str:
    return (
        "import json as __json\n"
        "import sys as __sys\n"
        "def __run():\n"
        "    __data = __json.loads(__sys.stdin.read())\n"
        "    __params = __data[\"params\"]\n"
        f"    __result = Solution().{function_name}(*__params)\n"
        "    print(__json.dumps(__result))\n"
        "__run()\n"
    )
