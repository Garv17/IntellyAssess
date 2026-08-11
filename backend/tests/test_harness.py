"""Tests for the function-signature judge (app.services.harness and
harness_langs). These run without Docker — build_program/compare are pure
string/JSON manipulation, and the Python driver is additionally executed
end-to-end via a real subprocess (skipped if python3 isn't on PATH, e.g. in a
minimal CI image). Java/C++/C codegen was manually verified against the real
eclipse-temurin/gcc sandbox images during development (including a matrix
param+return case for C); this suite guards the Python-side generation logic
and the type registries against drift."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from app.services import harness
from app.services.harness_langs import c, cpp, java, javascript, python

TWO_SUM_PARAMS = [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}]


# --------------------------------------------------------------- compare()


def test_compare_exact_match():
    ok, err = harness.compare("int[]", "[0,1]", [0, 1])
    assert ok is True
    assert err is None


def test_compare_wrong_answer():
    ok, err = harness.compare("int[]", "[0,2]", [0, 1])
    assert ok is False
    assert err is None


def test_compare_float_uses_epsilon_tolerance():
    ok, _ = harness.compare("float", "3.140000001", 3.14)
    assert ok is True
    ok, _ = harness.compare("float", "3.5", 3.14)
    assert ok is False


def test_compare_float_array_elementwise_tolerance():
    ok, _ = harness.compare("float[]", "[1.0000001, 2.0]", [1.0, 2.0])
    assert ok is True


def test_compare_malformed_output_is_a_parse_error_not_wrong_answer():
    ok, err = harness.compare("int[]", "not json", [0, 1])
    assert ok is False
    assert err is not None


# --------------------------------------------------------- encode_stdin()


def test_encode_stdin_wraps_params_in_json_object():
    assert json.loads(harness.encode_stdin([[2, 7, 11, 15], 9])) == {"params": [[2, 7, 11, 15], 9]}


# --------------------------------------------------------------- registry


@pytest.mark.parametrize("mod", [java, cpp])
def test_static_type_language_modules_cover_every_param_type(mod):
    # Catches the easy mistake of adding a type to PARAM_TYPES without adding the
    # matching decode/encode entry in a statically-typed language's codec map.
    assert set(mod._TYPES) == harness.PARAM_TYPES  # noqa: SLF001
    assert set(mod._DECODE) == harness.PARAM_TYPES  # noqa: SLF001
    assert set(mod._ENCODE) == harness.PARAM_TYPES  # noqa: SLF001


def test_python_type_hints_cover_every_param_type():
    assert set(python._TYPE_HINTS) == harness.PARAM_TYPES  # noqa: SLF001


def test_c_module_covers_every_param_type():
    # C splits decode/encode into scalar vs. array maps (matrix is handled
    # specially — it needs extra out-params, not a single decode/encode call),
    # so it gets its own completeness check rather than the shared one above.
    scalar_types = harness.PARAM_TYPES - c._ARRAY_TYPES - c._MATRIX_TYPES  # noqa: SLF001
    assert set(c._TYPES) == harness.PARAM_TYPES  # noqa: SLF001
    assert set(c._SCALAR_DECODE) == scalar_types  # noqa: SLF001
    assert set(c._SCALAR_ENCODE) == scalar_types  # noqa: SLF001
    assert set(c._ARRAY_DECODE) == c._ARRAY_TYPES  # noqa: SLF001
    assert set(c._ARRAY_ENCODE) == c._ARRAY_TYPES  # noqa: SLF001


def test_c_array_return_gets_returnsize_out_param():
    code = harness.generate_boilerplate("c", "twoSum", "int[]", TWO_SUM_PARAMS)
    assert "int* twoSum(int* nums, int numsSize, int target, int* returnSize)" in code


def test_c_matrix_return_gets_both_out_params():
    code = harness.generate_boilerplate("c", "transpose", "int[][]", [{"name": "matrix", "type": "int[][]"}])
    assert "int* returnSize" in code
    assert "int** returnColumnSizes" in code


def test_supported_function_languages_matches_language_modules():
    assert harness.SUPPORTED_FUNCTION_LANGUAGES == {"python", "javascript", "java", "cpp", "c"}


# ------------------------------------------------------ build_program() shape


def test_java_program_keeps_solution_non_public_even_if_student_typed_it():
    student_code = "public class Solution {\n    public int[] twoSum(int[] nums, int target) { return null; }\n}\n"
    program = harness.build_program("java", "twoSum", "int[]", TWO_SUM_PARAMS, student_code)
    assert "public class Solution" not in program
    assert program.count("public class Main") == 1


def test_cpp_boilerplate_passes_arrays_by_reference():
    code = harness.generate_boilerplate("cpp", "twoSum", "int[]", TWO_SUM_PARAMS)
    assert "vector<int>& nums" in code
    assert "int target" in code  # scalar stays by value


def test_javascript_boilerplate_has_no_type_annotations():
    code = harness.generate_boilerplate("javascript", "twoSum", "int[]", TWO_SUM_PARAMS)
    assert code.strip().startswith("function twoSum(nums, target)")


def test_unsupported_language_raises():
    with pytest.raises(ValueError, match="Function-signature"):
        harness.build_program("cpp2000", "twoSum", "int[]", TWO_SUM_PARAMS, "")


# --------------------------------------------------- end-to-end (Python only)

PYTHON3 = shutil.which("python3") or shutil.which("python") or sys.executable


@pytest.mark.skipif(not PYTHON3, reason="no python interpreter on PATH")
def test_python_program_runs_end_to_end(tmp_path):
    student_code = (
        "class Solution:\n"
        "    def twoSum(self, nums, target):\n"
        "        for i in range(len(nums)):\n"
        "            for j in range(i + 1, len(nums)):\n"
        "                if nums[i] + nums[j] == target:\n"
        "                    return [i, j]\n"
    )
    program = harness.build_program("python", "twoSum", "int[]", TWO_SUM_PARAMS, student_code)
    script = tmp_path / "main.py"
    script.write_text(program)

    stdin_data = harness.encode_stdin([[2, 7, 11, 15], 9])
    result = subprocess.run(
        [PYTHON3, str(script)], input=stdin_data, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    ok, err = harness.compare("int[]", result.stdout, [0, 1])
    assert ok is True, (result.stdout, err)
