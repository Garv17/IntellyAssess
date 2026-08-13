"""Creates and publishes a coding-only exam with exactly two SQL problems,
targeted at the STU-prefixed seeded cohort (cohort="2026", see scripts/seed.py),
for the "10 req/min Run for 10 minutes at 120 students" load test.

    python -m scripts.create_sql_loadtest_exam

Idempotent by title: re-running finds and re-publishes the existing exam instead
of creating a duplicate.
"""

from __future__ import annotations

import httpx

BASE_URL = "http://localhost:8000"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Admin@123"
EXAM_TITLE = "SQL Run Load Test"
COHORT = "2026"  # matches scripts.seed's seeded students

PROBLEMS = [
    {
        "body_md": "Find high scorers",
        "statement_md": (
            "### Find High Scorers\n\n"
            "You're given a `students(id, name, marks)` table. Write a single "
            "`SELECT` query returning the `name` of every student with "
            "`marks >= 70`, one per line, ordered alphabetically.\n\n"
            "The table already exists when your query runs — don't create it yourself."
        ),
        "allowed_languages": ["sql"],
        "time_limit_ms": 2000,
        "memory_limit_mb": 128,
        "starter_code": {"sql": "SELECT name FROM students WHERE marks >= 70 ORDER BY name;\n"},
        "sql_dialect": "sqlite",
        "sql_schema_sql": "CREATE TABLE students(id INTEGER, name TEXT, marks INTEGER);",
        "sql_result_columns": ["name"],
        "test_cases": [
            {
                "stdin": "INSERT INTO students VALUES (1,'Alice',80),(2,'Bob',65),(3,'Carol',90);",
                "expected_stdout": "Alice\nCarol",
                "is_sample": True,
                "weight": 1,
                "order_index": 0,
            },
            {
                "stdin": "INSERT INTO students VALUES (1,'Dan',72),(2,'Eve',50),(3,'Frank',99),(4,'Grace',70);",
                "expected_stdout": "Dan\nFrank\nGrace",
                "is_sample": False,
                "weight": 1,
                "order_index": 1,
            },
        ],
    },
    {
        "body_md": "Total sales per category",
        "statement_md": (
            "### Total Sales per Category\n\n"
            "You're given an `orders(id, category, amount)` table. Write a single "
            "`SELECT` query returning `category` and the `SUM(amount)` of orders in "
            "that category as `total`, one row per category, ordered alphabetically "
            "by category.\n\n"
            "The table already exists when your query runs — don't create it yourself."
        ),
        "allowed_languages": ["sql"],
        "time_limit_ms": 2000,
        "memory_limit_mb": 128,
        "starter_code": {
            "sql": "SELECT category, SUM(amount) AS total FROM orders GROUP BY category ORDER BY category;\n"
        },
        "sql_dialect": "sqlite",
        "sql_schema_sql": "CREATE TABLE orders(id INTEGER, category TEXT, amount INTEGER);",
        "sql_result_columns": ["category", "total"],
        "test_cases": [
            {
                "stdin": "INSERT INTO orders VALUES (1,'books',20),(2,'books',15),(3,'toys',40);",
                "expected_stdout": "books|35\ntoys|40",
                "is_sample": True,
                "weight": 1,
                "order_index": 0,
            },
            {
                "stdin": (
                    "INSERT INTO orders VALUES (1,'food',10),(2,'food',22),(3,'toys',5),"
                    "(4,'books',100);"
                ),
                "expected_stdout": "books|100\nfood|32\ntoys|5",
                "is_sample": False,
                "weight": 1,
                "order_index": 1,
            },
        ],
    },
]


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
                    "description": "Coding-only, SQL-only load test exam (2 problems).",
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
                json={"title": "SQL Problems", "marks_per_question": 10.0, "negative_marks": 0.0},
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
