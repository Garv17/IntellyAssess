"""Creates the schema and a demo exam: MCQ + DI + coding sections, an admin, and
a batch of students. Idempotent — safe to re-run.

    python -m scripts.seed
    python -m scripts.seed --students 400   # load-test sized cohort
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import (
    Admin,
    CodingProblem,
    DIGroup,
    Exam,
    ExamStatus,
    MCQOption,
    Question,
    QuestionType,
    Section,
    Student,
    TestCase,
)
from app.security import hash_password

DEMO_ADMIN = ("admin@example.com", "Admin User", "Admin@123")

MCQS = [
    (
        "What is the time complexity of binary search on a sorted array of n elements?",
        ["O(n)", "O(log n)", "O(n log n)", "O(1)"],
        1,
    ),
    (
        "Which data structure gives O(1) average-case lookup by key?",
        ["Linked list", "Binary search tree", "Hash table", "Sorted array"],
        2,
    ),
    (
        "In a relational database, which constraint prevents duplicate rows for a column pair?",
        ["CHECK", "UNIQUE", "DEFAULT", "NOT NULL"],
        1,
    ),
    (
        "HTTP status 409 indicates:",
        ["Not found", "Unauthorized", "Conflict with current state", "Server error"],
        2,
    ),
    (
        "Which of these is NOT a property of a database transaction?",
        ["Atomicity", "Consistency", "Isolation", "Idempotency"],
        3,
    ),
]

DI_QUESTIONS = [
    (
        "In which quarter was revenue highest?",
        ["Q1", "Q2", "Q3", "Q4"],
        3,
    ),
    (
        "What was the approximate percentage growth from Q1 to Q4?",
        ["25%", "40%", "60%", "75%"],
        2,
    ),
    (
        "Which quarter showed a decline compared to the previous quarter?",
        ["Q1", "Q2", "Q3", "None"],
        1,
    ),
]


async def seed(student_count: int) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        email, name, password = DEMO_ADMIN
        admin = (await db.execute(select(Admin).where(Admin.email == email))).scalar_one_or_none()
        if admin is None:
            admin = Admin(email=email, name=name, password_hash=hash_password(password))
            db.add(admin)
            await db.flush()
            print(f"admin created: {email} / {password}")

        existing = (
            await db.execute(select(Exam).where(Exam.title == "Demo Placement Test"))
        ).scalar_one_or_none()
        if existing is not None:
            print("demo exam already present; skipping exam seed")
        else:
            exam = Exam(
                title="Demo Placement Test",
                description="Sample exam covering aptitude, data interpretation and coding.",
                instructions_md=(
                    "- 60 minutes total\n"
                    "- Answers save automatically\n"
                    "- The exam submits itself when the timer reaches zero\n"
                    "- Do not close the browser; refreshing is safe"
                ),
                duration_minutes=60,
                status=ExamStatus.published,
                starts_at=datetime.now(UTC) - timedelta(minutes=5),
                ends_at=datetime.now(UTC) + timedelta(days=7),
                randomize_questions=True,
                randomize_options=True,
                cohort="2026",
                created_by=admin.id,
            )
            db.add(exam)
            await db.flush()

            # --- Section 1: MCQ
            mcq_section = Section(
                exam_id=exam.id,
                title="Aptitude & Fundamentals",
                instructions="Each question carries 2 marks. 0.5 marks are deducted for a wrong answer.",
                order_index=0,
                marks_per_question=2.0,
                negative_marks=0.5,
            )
            db.add(mcq_section)
            await db.flush()
            for idx, (body, options, correct) in enumerate(MCQS):
                question = Question(
                    section_id=mcq_section.id,
                    type=QuestionType.mcq,
                    body_md=body,
                    order_index=idx,
                )
                db.add(question)
                await db.flush()
                for oidx, option in enumerate(options):
                    db.add(
                        MCQOption(
                            question_id=question.id,
                            body=option,
                            is_correct=oidx == correct,
                            order_index=oidx,
                        )
                    )

            # --- Section 2: Data Interpretation
            di_section = Section(
                exam_id=exam.id,
                title="Data Interpretation",
                instructions="Study the chart, then answer all questions in the set.",
                order_index=1,
                marks_per_question=3.0,
                negative_marks=0.0,
            )
            db.add(di_section)
            await db.flush()

            group = DIGroup(
                section_id=di_section.id,
                title="Quarterly Revenue, FY25",
                passage_md=(
                    "| Quarter | Revenue (₹ Cr) |\n"
                    "|---|---|\n"
                    "| Q1 | 120 |\n"
                    "| Q2 | 118 |\n"
                    "| Q3 | 145 |\n"
                    "| Q4 | 168 |"
                ),
                order_index=0,
            )
            db.add(group)
            await db.flush()
            for idx, (body, options, correct) in enumerate(DI_QUESTIONS):
                question = Question(
                    section_id=di_section.id,
                    di_group_id=group.id,
                    type=QuestionType.di,
                    body_md=body,
                    order_index=idx,
                )
                db.add(question)
                await db.flush()
                for oidx, option in enumerate(options):
                    db.add(
                        MCQOption(
                            question_id=question.id,
                            body=option,
                            is_correct=oidx == correct,
                            order_index=oidx,
                        )
                    )

            # --- Section 3: Coding
            code_section = Section(
                exam_id=exam.id,
                title="Coding",
                instructions="Read input from stdin and print the result to stdout.",
                order_index=2,
                marks_per_question=15.0,
                negative_marks=0.0,
            )
            db.add(code_section)
            await db.flush()

            code_question = Question(
                section_id=code_section.id,
                type=QuestionType.coding,
                body_md="Sum of two integers",
                order_index=0,
            )
            db.add(code_question)
            await db.flush()

            problem = CodingProblem(
                question_id=code_question.id,
                statement_md=(
                    "### Sum of Two Integers\n\n"
                    "Read two space-separated integers from stdin and print their sum.\n\n"
                    "**Input**\n```\n3 4\n```\n\n**Output**\n```\n7\n```"
                ),
                allowed_languages=["python", "javascript", "c", "cpp", "java"],
                time_limit_ms=2000,
                memory_limit_mb=128,
                starter_code={
                    "python": "a, b = map(int, input().split())\nprint(a + b)\n",
                    "javascript": (
                        "const [a, b] = require('fs').readFileSync(0, 'utf8')"
                        ".trim().split(/\\s+/).map(Number);\nconsole.log(a + b);\n"
                    ),
                    "c": (
                        '#include <stdio.h>\nint main(){long long a,b;scanf("%lld %lld",&a,&b);'
                        'printf("%lld\\n",a+b);return 0;}\n'
                    ),
                    "cpp": (
                        "#include <iostream>\nint main(){long long a,b;"
                        "std::cin>>a>>b;std::cout<<a+b<<std::endl;return 0;}\n"
                    ),
                    "java": (
                        "import java.util.*;\npublic class Main{public static void main(String[] a){"
                        "Scanner s=new Scanner(System.in);"
                        "System.out.println(s.nextLong()+s.nextLong());}}\n"
                    ),
                },
            )
            db.add(problem)
            await db.flush()

            cases = [
                ("3 4", "7", True, 1),
                ("10 -2", "8", True, 1),
                ("0 0", "0", False, 1),
                ("1000000 2000000", "3000000", False, 2),
                ("-5 -7", "-12", False, 2),
            ]
            for idx, (stdin, expected, is_sample, weight) in enumerate(cases):
                db.add(
                    TestCase(
                        coding_problem_id=problem.id,
                        stdin=stdin,
                        expected_stdout=expected,
                        is_sample=is_sample,
                        weight=weight,
                        order_index=idx,
                    )
                )

            # SQL runs against a fresh in-memory DB per test case, so unlike the other
            # languages here, a test case's stdin is schema/setup SQL rather than
            # program input — see combine_stdin_with_code in services/sandbox.py.
            sql_question = Question(
                section_id=code_section.id,
                type=QuestionType.coding,
                body_md="Find high scorers",
                order_index=1,
            )
            db.add(sql_question)
            await db.flush()

            sql_problem = CodingProblem(
                question_id=sql_question.id,
                statement_md=(
                    "### Find High Scorers\n\n"
                    "You're given a `students(id, name, marks)` table. Write a single "
                    "`SELECT` query returning the `name` of every student with "
                    "`marks >= 70`, one per line, ordered alphabetically.\n\n"
                    "The table already exists when your query runs — don't create it "
                    "yourself."
                ),
                allowed_languages=["sql"],
                time_limit_ms=2000,
                memory_limit_mb=128,
                starter_code={"sql": "SELECT name FROM students WHERE marks >= 70 ORDER BY name;\n"},
            )
            db.add(sql_problem)
            await db.flush()

            sql_cases = [
                (
                    "CREATE TABLE students(id INTEGER, name TEXT, marks INTEGER);\n"
                    "INSERT INTO students VALUES (1,'Alice',80),(2,'Bob',65),(3,'Carol',90);",
                    "Alice\nCarol",
                    True,
                    1,
                ),
                (
                    "CREATE TABLE students(id INTEGER, name TEXT, marks INTEGER);\n"
                    "INSERT INTO students VALUES (1,'Dan',72),(2,'Eve',50),(3,'Frank',99),"
                    "(4,'Grace',70);",
                    "Dan\nFrank\nGrace",
                    False,
                    1,
                ),
            ]
            for idx, (stdin, expected, is_sample, weight) in enumerate(sql_cases):
                db.add(
                    TestCase(
                        coding_problem_id=sql_problem.id,
                        stdin=stdin,
                        expected_stdout=expected,
                        is_sample=is_sample,
                        weight=weight,
                        order_index=idx,
                    )
                )

            # A second SQL problem: multi-table GROUP BY / HAVING with several
            # boundary-condition hidden cases (exact-threshold spend, single-category
            # high spend, duplicate purchases pushing a customer over the line).
            multi_table_question = Question(
                section_id=code_section.id,
                type=QuestionType.coding,
                body_md="Customers spanning categories with high spend",
                order_index=2,
            )
            db.add(multi_table_question)
            await db.flush()

            multi_table_problem = CodingProblem(
                question_id=multi_table_question.id,
                statement_md=(
                    "### High-Value Multi-Category Customers\n\n"
                    "Three tables already exist when your query runs:\n\n"
                    "```sql\n"
                    "CREATE TABLE Customers (\n"
                    "    customer_id INT PRIMARY KEY,\n"
                    "    customer_name VARCHAR(50)\n"
                    ");\n\n"
                    "CREATE TABLE Products (\n"
                    "    product_id VARCHAR(5) PRIMARY KEY,\n"
                    "    category VARCHAR(50)\n"
                    ");\n\n"
                    "CREATE TABLE Orders (\n"
                    "    order_id INT PRIMARY KEY,\n"
                    "    customer_id INT REFERENCES Customers(customer_id),\n"
                    "    product_id VARCHAR(5) REFERENCES Products(product_id),\n"
                    "    amount INT\n"
                    ");\n"
                    "```\n\n"
                    "Write a single `SELECT` query returning the `customer_name` of every "
                    "customer who has purchased products from **at least 2 different "
                    "categories** AND whose **total spend is more than ₹10,000**.\n\n"
                    "Don't create the tables yourself — they're already populated."
                ),
                allowed_languages=["sql"],
                time_limit_ms=2000,
                memory_limit_mb=128,
                starter_code={
                    "sql": (
                        "SELECT c.customer_name\n"
                        "FROM   Orders o\n"
                        "JOIN   Customers c ON o.customer_id = c.customer_id\n"
                        "JOIN   Products  p ON o.product_id  = p.product_id\n"
                        "GROUP BY c.customer_id, c.customer_name\n"
                        "HAVING COUNT(DISTINCT p.category) >= 2\n"
                        "   AND SUM(o.amount) > 10000;\n"
                    )
                },
            )
            db.add(multi_table_problem)
            await db.flush()

            multi_table_ddl = (
                "CREATE TABLE Customers (customer_id INT PRIMARY KEY, customer_name VARCHAR(50));\n"
                "CREATE TABLE Products (product_id VARCHAR(5) PRIMARY KEY, category VARCHAR(50));\n"
                "CREATE TABLE Orders (order_id INT PRIMARY KEY, "
                "customer_id INT REFERENCES Customers(customer_id), "
                "product_id VARCHAR(5) REFERENCES Products(product_id), amount INT);\n"
            )
            multi_table_base_data = (
                "INSERT INTO Customers (customer_id, customer_name) VALUES "
                "(1,'Aarav'),(2,'Bhavna'),(3,'Chetan'),(4,'Divya');\n"
                "INSERT INTO Products (product_id, category) VALUES "
                "('P1','Electronics'),('P2','Electronics'),('P3','Clothing'),"
                "('P4','Grocery'),('P5','Clothing');\n"
                "INSERT INTO Orders (order_id, customer_id, product_id, amount) VALUES "
                "(1,1,'P1',6000),(2,1,'P3',3000),(3,1,'P4',2000),(4,2,'P1',8000),"
                "(5,2,'P2',5000),(6,3,'P1',4000),(7,3,'P3',3000),(8,4,'P2',7000),"
                "(9,4,'P5',6000);\n"
            )

            multi_table_cases = [
                # Base data only -> Aarav (14k/2 cat), Divya (13k/2 cat); Bhavna and
                # Chetan each stay within a single category or under the spend bar.
                (
                    multi_table_ddl + multi_table_base_data,
                    "Aarav\nDivya",
                    True,
                    1,
                ),
                # Edge case 1: Esha spends exactly 10,000 across 2 categories -> the
                # strict "> 10000" must exclude her; result is unchanged.
                (
                    multi_table_ddl + multi_table_base_data
                    + "INSERT INTO Customers VALUES (5,'Esha');\n"
                    "INSERT INTO Orders VALUES (10,5,'P1',6000),(11,5,'P3',4000);\n",
                    "Aarav\nDivya",
                    False,
                    1,
                ),
                # Edge case 2: Farhan spends 12,001 but in a single category ->
                # the >=2-categories condition must exclude him.
                (
                    multi_table_ddl + multi_table_base_data
                    + "INSERT INTO Customers VALUES (6,'Farhan');\n"
                    "INSERT INTO Orders VALUES (12,6,'P4',12000);\n",
                    "Aarav\nDivya",
                    False,
                    1,
                ),
                # Edge case 3: a duplicate purchase pushes Chetan's total spend over
                # 10,000 while he already had 2 categories -> he now qualifies.
                (
                    multi_table_ddl + multi_table_base_data
                    + "INSERT INTO Orders VALUES (14,3,'P1',4000);\n",
                    "Aarav\nChetan\nDivya",
                    False,
                    2,
                ),
                # All edge cases combined.
                (
                    multi_table_ddl + multi_table_base_data
                    + "INSERT INTO Customers VALUES (5,'Esha'),(6,'Farhan');\n"
                    "INSERT INTO Orders VALUES (10,5,'P1',6000),(11,5,'P3',4000),"
                    "(12,6,'P4',12000),(14,3,'P1',4000);\n",
                    "Aarav\nChetan\nDivya",
                    False,
                    2,
                ),
            ]
            for idx, (stdin, expected, is_sample, weight) in enumerate(multi_table_cases):
                db.add(
                    TestCase(
                        coding_problem_id=multi_table_problem.id,
                        stdin=stdin,
                        expected_stdout=expected,
                        is_sample=is_sample,
                        weight=weight,
                        order_index=idx,
                    )
                )

            print(f"demo exam created: {exam.id}")

        # --- Students
        existing_ids = set(
            (await db.execute(select(Student.student_id))).scalars()
        )
        created = 0
        for i in range(1, student_count + 1):
            sid = f"STU{i:04d}"
            if sid in existing_ids:
                continue
            db.add(
                Student(
                    student_id=sid,
                    name=f"Student {i:04d}",
                    email=f"{sid.lower()}@example.com",
                    cohort="2026",
                )
            )
            created += 1

        await db.commit()
        print(f"students created: {created}")
        print("students sign in via magic link sent to their email, e.g. stu0001@example.com")


async def seed_loadtest(student_count: int, duration_minutes: int) -> None:
    """A dedicated exam + student pool for scripts/loadtest.py and ramp_loadtest.py,
    kept separate from the demo cohort so a load test never collides with (or
    inflates) real-looking data. LT-prefixed ids and cohort="loadtest" only ever
    show up here."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        email, name, password = DEMO_ADMIN
        admin = (await db.execute(select(Admin).where(Admin.email == email))).scalar_one_or_none()
        if admin is None:
            admin = Admin(email=email, name=name, password_hash=hash_password(password))
            db.add(admin)
            await db.flush()
            print(f"admin created: {email} / {password}")

        exam = (
            await db.execute(select(Exam).where(Exam.title == "Load Test Exam"))
        ).scalar_one_or_none()
        if exam is not None:
            # Duration/window may need to grow for a longer run than last time.
            exam.duration_minutes = duration_minutes
            exam.ends_at = datetime.now(UTC) + timedelta(days=1)
            exam.status = ExamStatus.published
            print(f"load test exam already present; duration set to {duration_minutes}m")
        else:
            exam = Exam(
                title="Load Test Exam",
                description="Synthetic exam used only by scripts/loadtest.py.",
                instructions_md="Answers save automatically. This exam is not scored.",
                duration_minutes=duration_minutes,
                status=ExamStatus.published,
                starts_at=datetime.now(UTC) - timedelta(minutes=5),
                ends_at=datetime.now(UTC) + timedelta(days=1),
                randomize_questions=False,
                randomize_options=False,
                cohort="loadtest",
                created_by=admin.id,
            )
            db.add(exam)
            await db.flush()

            section = Section(
                exam_id=exam.id,
                title="Section",
                order_index=0,
                marks_per_question=1.0,
                negative_marks=0.0,
            )
            db.add(section)
            await db.flush()
            for idx, (body, options, correct) in enumerate(MCQS):
                question = Question(
                    section_id=section.id, type=QuestionType.mcq, body_md=body, order_index=idx
                )
                db.add(question)
                await db.flush()
                for oidx, option in enumerate(options):
                    db.add(
                        MCQOption(
                            question_id=question.id,
                            body=option,
                            is_correct=oidx == correct,
                            order_index=oidx,
                        )
                    )
            print(f"load test exam created: {exam.id} ({duration_minutes}m, cohort=loadtest)")

        # Added after the fact for exams seeded before coding support — checked every
        # run so scripts/loadtest.py always has a coding question to save code_text
        # against, not just MCQ options.
        coding_section = (
            await db.execute(
                select(Section).where(Section.exam_id == exam.id, Section.title == "Coding")
            )
        ).scalar_one_or_none()
        if coding_section is None:
            coding_section = Section(
                exam_id=exam.id,
                title="Coding",
                order_index=1,
                marks_per_question=10.0,
                negative_marks=0.0,
            )
            db.add(coding_section)
            await db.flush()

            code_question = Question(
                section_id=coding_section.id,
                type=QuestionType.coding,
                body_md="Sum of two integers",
                order_index=0,
            )
            db.add(code_question)
            await db.flush()

            problem = CodingProblem(
                question_id=code_question.id,
                statement_md=(
                    "Read two space-separated integers from stdin and print their sum."
                ),
                allowed_languages=["python"],
                time_limit_ms=2000,
                memory_limit_mb=128,
                starter_code={"python": "a, b = map(int, input().split())\nprint(a + b)\n"},
            )
            db.add(problem)
            await db.flush()

            for idx, (stdin, expected, is_sample, weight) in enumerate(
                [("3 4", "7", True, 1), ("10 -2", "8", False, 1)]
            ):
                db.add(
                    TestCase(
                        coding_problem_id=problem.id,
                        stdin=stdin,
                        expected_stdout=expected,
                        is_sample=is_sample,
                        weight=weight,
                        order_index=idx,
                    )
                )
            print("load test exam: coding section added")

        existing_ids = set(
            (await db.execute(select(Student.student_id))).scalars()
        )
        created = 0
        for i in range(1, student_count + 1):
            sid = f"LT{i:04d}"
            if sid in existing_ids:
                continue
            db.add(
                Student(
                    student_id=sid,
                    name=f"Load Test {i:04d}",
                    email=f"{sid.lower()}@example.com",
                    cohort="loadtest",
                )
            )
            created += 1

        await db.commit()
        print(f"load test students created: {created} (LT0001..LT{student_count:04d})")


async def _run(students: int, loadtest: bool, loadtest_students: int, loadtest_duration: int) -> None:
    # Both steps share the module-level async engine, which is bound to whichever
    # event loop first used it — so they must run inside one asyncio.run(), not two.
    await seed(students)
    if loadtest:
        await seed_loadtest(loadtest_students, loadtest_duration)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--students", type=int, default=25)
    parser.add_argument(
        "--loadtest", action="store_true", help="also seed the LT-prefixed loadtest cohort/exam"
    )
    parser.add_argument("--loadtest-students", type=int, default=250)
    parser.add_argument(
        "--loadtest-duration", type=int, default=120, help="minutes; must exceed the planned run length"
    )
    args = parser.parse_args()
    asyncio.run(_run(args.students, args.loadtest, args.loadtest_students, args.loadtest_duration))
