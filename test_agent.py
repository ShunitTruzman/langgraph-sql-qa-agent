"""
Unit tests for the LangGraph QA Agent.
Covers:
  1. Database queries and joins
  2. SQL generation from natural language
  3. End-to-end agent behavior

Run with: pytest test_agent.py -v
"""

import os
import json
import sqlite3
import pytest
from qa_agent import (
    AgentConfig, _parse_json, _schema_text, _sql_prompt,
    build_app, run_question,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    sql_path = os.path.join(os.path.dirname(__file__),
                            "university_schema_and_seed.sql")
    with open(sql_path, encoding="utf-8") as f:
        db.executescript(f.read())
    yield db
    db.close()

def sql_resp(sql):
    return json.dumps({"type": "sql", "sql": sql, "params": {}})

def clarify_resp(q):
    return json.dumps({"type": "clarify", "question": q})

class FakeLLM:
    def __init__(self, sql_responses, answer="Answer."):
        self._queue = list(sql_responses)
        self._answer = answer
        self.sql_calls = []

    def __call__(self, prompt):
        if "Convert the user question" in prompt:
            self.sql_calls.append(prompt)
            return self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        return self._answer


# ── 1. Database queries and joins ──────────────────────────────────────────────

def test_teacher_joined_to_cs101_spring_2026(conn):
    """3-table JOIN: CS101 Spring 2026 must be taught by Dr. Alice Nguyen."""
    row = conn.execute("""
        SELECT t.name FROM course_offerings o
        JOIN teachers t ON t.teacher_id = o.teacher_id
        JOIN courses c ON c.course_id = o.course_id
        WHERE c.code='CS101' AND o.semester='Spring' AND o.year=2026
    """).fetchone()
    assert row[0] == "Dr. Alice Nguyen"

def test_average_grade_cs101_spring_2026(conn):
    """AVG grade for CS101 Spring 2026 should be (94+87)/2 = 90.5."""
    row = conn.execute("""
        SELECT ROUND(AVG(e.grade), 2) FROM enrollments e
        JOIN course_offerings o ON o.offering_id = e.offering_id
        JOIN courses c ON c.course_id = o.course_id
        WHERE c.code='CS101' AND o.semester='Spring' AND o.year=2026
    """).fetchone()
    assert row[0] == 90.5

def test_student_enrollment_count(conn):
    """Maya Patel should be enrolled in 3 courses total."""
    row = conn.execute("""
        SELECT COUNT(*) FROM enrollments e
        JOIN students s ON s.student_id = e.student_id
        WHERE s.name='Maya Patel'
    """).fetchone()
    assert row[0] == 3

def test_unique_enrollment_constraint(conn):
    """Duplicate enrollment (same student + offering) must raise IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO enrollments (student_id, offering_id, grade) VALUES (1, 1, 70)")
        conn.commit()


# ── 2. SQL generation from natural language ────────────────────────────────────

def test_parse_json_clean():
    assert _parse_json('{"type":"sql","sql":"SELECT 1"}')["type"] == "sql"

def test_parse_json_with_extra_text():
    """LLM sometimes wraps JSON in prose — parser should still extract it."""
    obj = _parse_json('Sure! {"type":"clarify","question":"Which semester?"} done.')
    assert obj["type"] == "clarify"

def test_parse_json_raises_on_garbage():
    with pytest.raises(Exception):
        _parse_json("not json at all")

def test_schema_text_contains_all_tables(conn):
    schema = _schema_text(conn)
    for t in ["teachers", "students", "courses", "course_offerings", "enrollments"]:
        assert t in schema

def test_sql_prompt_includes_repair_on_error():
    p = _sql_prompt("q", "schema", last_error="no such table: foo")
    assert "no such table: foo" in p and "Fix it" in p

def test_gen_sql_rejects_non_select_and_retries(conn):
    """DELETE must be rejected; agent should retry and succeed with valid SELECT."""
    llm = FakeLLM([
        sql_resp("DELETE FROM teachers"),       # rejected
        sql_resp("SELECT name FROM teachers"),  # repair
    ])
    out = build_app(conn=conn, llm=llm, config=AgentConfig(max_attempts=2)).invoke(
        {"question": "q", "trace": [], "attempts": 0}
    )
    assert out.get("rows") is not None

def test_gen_sql_appends_limit_when_missing(conn):
    llm = FakeLLM([sql_resp("SELECT name FROM teachers")])
    out = build_app(conn=conn, llm=llm, config=AgentConfig(max_rows=10)).invoke(
        {"question": "q", "trace": [], "attempts": 0}
    )
    assert "limit" in out["sql"].lower()


# ── 3. End-to-end agent behavior ───────────────────────────────────────────────

def test_e2e_happy_path(conn):
    """Full flow: question → SQL → DB result → answer."""
    llm = FakeLLM(
        [sql_resp("SELECT t.name FROM teachers t "
                  "JOIN course_offerings o ON o.teacher_id=t.teacher_id "
                  "JOIN courses c ON c.course_id=o.course_id "
                  "WHERE c.code='CS101' AND o.semester='Spring' AND o.year=2026")],
        answer="Dr. Alice Nguyen teaches CS101.",
    )
    ans, trace, state = run_question(conn=conn, llm=llm, question="Who teaches CS101 Spring 2026?")
    assert state["rows"]
    assert isinstance(ans, str) and len(ans) > 0

def test_e2e_retry_passes_error_to_llm(conn):
    """On DB error, the retry prompt must include the original error message."""
    llm = FakeLLM([
        sql_resp("SELECT * FROM fake_table"),   # causes DB error
        sql_resp("SELECT name FROM teachers"),  # repair
    ])
    run_question(conn=conn, llm=llm, question="q", config=AgentConfig(max_attempts=2))
    assert "no such table" in llm.sql_calls[1].lower()

def test_e2e_retries_exhausted_returns_error_message(conn):
    """When all retries fail, answer must be a user-friendly error string."""
    llm = FakeLLM([sql_resp("SELECT * FROM fake_table")])
    ans, _, _ = run_question(conn=conn, llm=llm, question="q", config=AgentConfig(max_attempts=1))
    assert "Couldn't run a valid SQL query" in ans

def test_e2e_clarify_skips_sql_execution(conn):
    """Clarify decision must short-circuit before any SQL is run."""
    llm = FakeLLM([clarify_resp("Which semester?")])
    ans, _, state = run_question(conn=conn, llm=llm, question="Tell me about courses")
    assert state["decision"] == "clarify"
    assert "Which semester?" in ans
    assert state.get("rows") is None

def test_e2e_trace_contains_key_nodes(conn):
    """Trace must record load_schema, gen_sql, exec_sql, and answer nodes."""
    llm = FakeLLM([sql_resp("SELECT name FROM teachers")])
    _, trace, _ = run_question(conn=conn, llm=llm, question="q")
    nodes = {e["node"] for e in trace}
    assert {"load_schema", "gen_sql", "exec_sql", "answer"}.issubset(nodes)