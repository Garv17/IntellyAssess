"""Creates and publishes a coding-only exam with exactly two problems — a DSA
question (second largest number, stdio mode, Python/JS/C/C++/Java) and a SQL
question (second highest salary) — targeted at the STU-prefixed seeded cohort
(cohort="2026", see scripts/seed.py), for the 1200-req/min multi-language Run
load test.

    python -m scripts.create_dsa_sql_loadtest_exam

Idempotent by title: re-running finds and re-publishes the existing exam instead
of creating a duplicate.
"""

from __future__ import annotations

import httpx

BASE_URL = "http://localhost:8000"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Admin@123"
EXAM_TITLE = "DSA + SQL Run Load Test"
COHORT = "2026"  # matches scripts.seed's seeded students

# "Second largest number" = the largest value strictly less than the maximum
# (duplicates of the max don't count as a second value) — stated explicitly in
# the problem so there's no ambiguity about which classic variant is meant.
DSA_PROBLEM = {
    "body_md": "Second largest number",
    "statement_md": (
        "### Second Largest Number\n\n"
        "Read a single line of space-separated integers. Print the **second "
        "largest** value — the largest value that is strictly less than the "
        "maximum (a repeated maximum only counts once).\n\n"
        "The input always contains at least two distinct values."
    ),
    "allowed_languages": ["python", "javascript", "c", "cpp", "java"],
    "time_limit_ms": 2000,
    "memory_limit_mb": 256,
    "starter_code": {
        "python": (
            "import sys\n"
            "nums = list(map(int, sys.stdin.read().split()))\n"
            "mx = second = float('-inf')\n"
            "for x in nums:\n"
            "    if x > mx:\n"
            "        second, mx = mx, x\n"
            "    elif mx > x > second:\n"
            "        second = x\n"
            "print(second)\n"
        ),
        "javascript": (
            "const nums = require('fs').readFileSync(0, 'utf8').trim().split(/\\s+/).map(Number);\n"
            "let mx = -Infinity, second = -Infinity;\n"
            "for (const x of nums) {\n"
            "  if (x > mx) { second = mx; mx = x; }\n"
            "  else if (x < mx && x > second) { second = x; }\n"
            "}\n"
            "console.log(second);\n"
        ),
        "c": (
            "#include <stdio.h>\n"
            "#include <limits.h>\n"
            "int main(){\n"
            "    long x, mx = LONG_MIN, second = LONG_MIN;\n"
            "    while (scanf(\"%ld\", &x) == 1) {\n"
            "        if (x > mx) { second = mx; mx = x; }\n"
            "        else if (x < mx && x > second) { second = x; }\n"
            "    }\n"
            "    printf(\"%ld\", second);\n"
            "    return 0;\n"
            "}\n"
        ),
        "cpp": (
            "#include <iostream>\n"
            "#include <climits>\n"
            "int main(){\n"
            "    long long x, mx = LLONG_MIN, second = LLONG_MIN;\n"
            "    while (std::cin >> x) {\n"
            "        if (x > mx) { second = mx; mx = x; }\n"
            "        else if (x < mx && x > second) { second = x; }\n"
            "    }\n"
            "    std::cout << second;\n"
            "    return 0;\n"
            "}\n"
        ),
        "java": (
            "import java.util.Scanner;\n"
            "public class Main {\n"
            "    public static void main(String[] args) {\n"
            "        Scanner sc = new Scanner(System.in);\n"
            "        long mx = Long.MIN_VALUE, second = Long.MIN_VALUE;\n"
            "        while (sc.hasNextLong()) {\n"
            "            long x = sc.nextLong();\n"
            "            if (x > mx) { second = mx; mx = x; }\n"
            "            else if (x < mx && x > second) { second = x; }\n"
            "        }\n"
            "        System.out.print(second);\n"
            "    }\n"
            "}\n"
        ),
    },
    "test_cases": [
        {
            "stdin": "5 3 9 1 9 7",
            "expected_stdout": "7",
            "is_sample": True,
            "weight": 1,
            "order_index": 0,
        },
        {
            "stdin": "10 10 8 6",
            "expected_stdout": "8",
            "is_sample": False,
            "weight": 1,
            "order_index": 1,
        },
    ],
}

SQL_PROBLEM = {
    "body_md": "Second highest salary",
    "statement_md": (
        "### Second Highest Salary\n\n"
        "You're given an `employees(id, name, salary)` table. Write a single "
        "`SELECT` query returning the second highest **distinct** salary (a "
        "salary tied for highest only counts once).\n\n"
        "The table already exists when your query runs — don't create it yourself."
    ),
    "allowed_languages": ["sql"],
    "time_limit_ms": 2000,
    "memory_limit_mb": 128,
    "starter_code": {
        "sql": "SELECT MAX(salary) FROM employees WHERE salary < (SELECT MAX(salary) FROM employees);\n"
    },
    "sql_dialect": "sqlite",
    "sql_schema_sql": "CREATE TABLE employees(id INTEGER, name TEXT, salary INTEGER);",
    "sql_result_columns": ["salary"],
    "test_cases": [
        {
            "stdin": "INSERT INTO employees VALUES (1,'Alice',80000),(2,'Bob',65000),(3,'Carol',95000);",
            "expected_stdout": "80000",
            "is_sample": True,
            "weight": 1,
            "order_index": 0,
        },
        {
            "stdin": (
                "INSERT INTO employees VALUES (1,'Dan',50000),(2,'Eve',50000),"
                "(3,'Frank',90000),(4,'Grace',70000);"
            ),
            "expected_stdout": "70000",
            "is_sample": False,
            "weight": 1,
            "order_index": 1,
        },
    ],
}

PROBLEMS = [DSA_PROBLEM, SQL_PROBLEM]


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        login = client.post(
            "/api/auth/admin/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        existing = client.get("/api/admin/exams", headers=headers)
        existing.raise_for_status()
        match = next((e for e in existing.json() if e["title"] == EXAM_TITLE), None)

        if match is None:
            exam = client.post(
                "/api/admin/exams",
                headers=headers,
                json={
                    "title": EXAM_TITLE,
                    "description": "Coding-only load test exam: 1 DSA (multi-language) + 1 SQL problem.",
                    "duration_minutes": 30,
                    "randomize_questions": False,
                    "randomize_options": False,
                    "cohort": COHORT,
                },
            )
            exam.raise_for_status()
            exam_id = exam.json()["id"]
            print(f"exam created: {exam_id}")

            section = client.post(
                f"/api/admin/exams/{exam_id}/sections",
                headers=headers,
                json={"title": "DSA + SQL", "marks_per_question": 10.0, "negative_marks": 0.0},
            )
            section.raise_for_status()
            section_id = section.json()["id"]

            for problem in PROBLEMS:
                created = client.post(
                    f"/api/admin/sections/{section_id}/coding", headers=headers, json=problem
                )
                created.raise_for_status()
                print(f"  coding question created: {created.json()}")
        else:
            exam_id = match["id"]
            print(f"exam already exists: {exam_id} (status={match['status']})")

        publish = client.post(f"/api/admin/exams/{exam_id}/publish", headers=headers)
        if publish.status_code >= 400:
            print(f"publish failed: {publish.status_code} {publish.text}")
            publish.raise_for_status()
        print(f"exam published: {exam_id}")


if __name__ == "__main__":
    main()
