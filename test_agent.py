"""
Unit tests for the LangGraph QA Agent (e-commerce database).
Covers:
  1. Database queries and joins
  2. SQL generation from natural language (incl. structured-output schema)
  3. End-to-end agent behavior
  4. Human-in-the-loop clarification (interrupt + resume)

Run with: pytest test_agent.py -v
"""

import os
import json
import sqlite3
import pytest
from qa_agent import (
    AgentConfig, SQL_DECISION_SCHEMA, _parse_json, _schema_text, _sql_prompt,
    build_app, run_question,
)

# A reusable run config — build_app compiles with a checkpointer, so direct
# .invoke() calls must supply a thread_id.
CFG = {"configurable": {"thread_id": "test-thread"}}

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    sql_path = os.path.join(os.path.dirname(__file__),
                            "ecommerce_schema_and_seed.sql")
    with open(sql_path, encoding="utf-8") as f:
        db.executescript(f.read())
    yield db
    db.close()

def sql_resp(sql):
    return json.dumps({"type": "sql", "sql": sql, "question": None})

def clarify_resp(q):
    return json.dumps({"type": "clarify", "sql": None, "question": q})

class FakeLLM:
    """Test double. Returns queued JSON for SQL-gen calls, a fixed string otherwise."""
    def __init__(self, sql_responses, answer="Answer."):
        self._queue = list(sql_responses)
        self._answer = answer
        self.sql_calls = []
        self.schemas = []  # records the response_schema passed on each SQL-gen call

    def __call__(self, prompt, *, response_schema=None):
        if "Convert the user question" in prompt:
            self.sql_calls.append(prompt)
            self.schemas.append(response_schema)
            return self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        return self._answer


# ── 1. Database queries and joins ──────────────────────────────────────────────

def test_customer_order_count(conn):
    """Alice Cohen placed 3 orders (a customer↔orders join)."""
    row = conn.execute("""
        SELECT COUNT(*) FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        WHERE c.name = 'Alice Cohen'
    """).fetchone()
    assert row[0] == 3

def test_total_quantity_of_product_sold(conn):
    """'SQL for Beginners' (BOOK-001) sold 3 units total across all orders."""
    row = conn.execute("""
        SELECT SUM(oi.quantity) FROM order_items oi
        JOIN products p ON p.product_id = oi.product_id
        WHERE p.sku = 'BOOK-001'
    """).fetchone()
    assert row[0] == 3

def test_average_price_in_electronics(conn):
    """Average price of Electronics products = (120+25+80+45)/4 = 67.5."""
    row = conn.execute("""
        SELECT ROUND(AVG(p.price), 2) FROM products p
        JOIN categories c ON c.category_id = p.category_id
        WHERE c.name = 'Electronics'
    """).fetchone()
    assert row[0] == 67.5

def test_unique_order_item_constraint(conn):
    """A product can appear at most once per order — duplicate must raise IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price) "
            "VALUES (1, 1, 1, 120.00)"  # order 1 already contains product 1
        )
        conn.commit()


# ── 2. SQL generation from natural language ────────────────────────────────────

def test_parse_json_clean():
    assert _parse_json('{"type":"sql","sql":"SELECT 1"}')["type"] == "sql"

def test_parse_json_with_extra_text():
    """LLM sometimes wraps JSON in prose — parser should still extract it."""
    obj = _parse_json('Sure! {"type":"clarify","question":"Which category?"} done.')
    assert obj["type"] == "clarify"

def test_parse_json_raises_on_garbage():
    with pytest.raises(Exception):
        _parse_json("not json at all")

def test_schema_text_contains_all_tables(conn):
    schema = _schema_text(conn)
    for t in ["customers", "categories", "products", "orders", "order_items"]:
        assert t in schema

def test_sql_prompt_includes_repair_on_error():
    p = _sql_prompt("q", "schema", last_error="no such table: foo")
    assert "no such table: foo" in p and "Fix it" in p

def test_gen_sql_passes_structured_output_schema(conn):
    """SQL-generation calls must request the strict structured-output schema."""
    llm = FakeLLM([sql_resp("SELECT name FROM customers")])
    run_question(conn=conn, llm=llm, question="q")
    assert llm.schemas[0] is SQL_DECISION_SCHEMA
    assert llm.schemas[0]["strict"] is True
    assert llm.schemas[0]["name"] == "sql_decision"

def test_gen_sql_rejects_non_select_and_retries(conn):
    """DELETE must be rejected; agent should retry and succeed with valid SELECT."""
    llm = FakeLLM([
        sql_resp("DELETE FROM customers"),       # rejected
        sql_resp("SELECT name FROM customers"),  # repair
    ])
    out = build_app(conn=conn, llm=llm, config=AgentConfig(max_attempts=2)).invoke(
        {"question": "q", "trace": [], "attempts": 0, "clarifications": 0}, config=CFG
    )
    assert out.get("rows") is not None

def test_gen_sql_appends_limit_when_missing(conn):
    llm = FakeLLM([sql_resp("SELECT name FROM customers")])
    out = build_app(conn=conn, llm=llm, config=AgentConfig(max_rows=10)).invoke(
        {"question": "q", "trace": [], "attempts": 0, "clarifications": 0}, config=CFG
    )
    assert "limit" in out["sql"].lower()


# ── 3. End-to-end agent behavior ───────────────────────────────────────────────

def test_e2e_happy_path(conn):
    """Full flow: question → SQL → DB result → answer."""
    llm = FakeLLM(
        [sql_resp("SELECT c.name FROM orders o "
                  "JOIN customers c ON c.customer_id = o.customer_id "
                  "WHERE o.order_id = 1")],
        answer="Alice Cohen placed order #1.",
    )
    ans, trace, state = run_question(conn=conn, llm=llm, question="Who placed order 1?")
    assert state["rows"]
    assert isinstance(ans, str) and len(ans) > 0

def test_e2e_retry_passes_error_to_llm(conn):
    """On DB error, the retry prompt must include the original error message."""
    llm = FakeLLM([
        sql_resp("SELECT * FROM fake_table"),     # causes DB error
        sql_resp("SELECT name FROM customers"),   # repair
    ])
    run_question(conn=conn, llm=llm, question="q", config=AgentConfig(max_attempts=2))
    assert "no such table" in llm.sql_calls[1].lower()

def test_e2e_retries_exhausted_returns_error_message(conn):
    """When all retries fail, answer must be a user-friendly error string."""
    llm = FakeLLM([sql_resp("SELECT * FROM fake_table")])
    ans, _, _ = run_question(conn=conn, llm=llm, question="q", config=AgentConfig(max_attempts=1))
    assert "Couldn't run a valid SQL query" in ans

def test_e2e_trace_contains_key_nodes(conn):
    """Trace must record load_schema, gen_sql, exec_sql, and answer nodes."""
    llm = FakeLLM([sql_resp("SELECT name FROM customers")])
    _, trace, _ = run_question(conn=conn, llm=llm, question="q")
    nodes = {e["node"] for e in trace}
    assert {"load_schema", "gen_sql", "exec_sql", "answer"}.issubset(nodes)


# ── 4. Human-in-the-loop clarification ─────────────────────────────────────────

def test_clarify_interrupts_without_callback(conn):
    """With no on_clarify callback, the agent surfaces the question and does not run SQL."""
    llm = FakeLLM([clarify_resp("Which category?")])
    ans, _, out = run_question(conn=conn, llm=llm, question="Tell me about products")
    assert "Which category?" in ans
    assert out.get("rows") is None

def test_clarify_resumes_with_callback(conn):
    """
    Full human-in-the-loop loop:
    clarify → pause → user reply → resume → SQL → answer.
    """
    llm = FakeLLM(
        [clarify_resp("Which category?"), sql_resp("SELECT name FROM customers")],
        answer="Here are the customers.",
    )
    seen = []
    def on_clarify(q):
        seen.append(q)
        return "Electronics"

    ans, trace, state = run_question(
        conn=conn, llm=llm, question="Tell me about products", on_clarify=on_clarify
    )
    assert seen and "Which category?" in seen[0]       # the user was asked
    assert "Electronics" in state["question"]           # reply folded into the question
    assert state.get("rows") is not None                # SQL ran after resume
    assert ans == "Here are the customers."             # final grounded answer
    assert any(e["node"] == "clarify" for e in trace)   # clarify node recorded in trace

def test_clarify_budget_is_bounded(conn):
    """If the LLM keeps asking to clarify, the agent stops after max_clarifications."""
    llm = FakeLLM([clarify_resp("Still ambiguous?")])  # always clarifies
    calls = {"n": 0}
    def on_clarify(q):
        calls["n"] += 1
        return "some reply"

    ans, _, _ = run_question(
        conn=conn, llm=llm, question="Tell me about products",
        on_clarify=on_clarify, config=AgentConfig(max_clarifications=2),
    )
    assert calls["n"] == 2                 # asked exactly twice, then gave up
    assert "Still ambiguous?" in ans       # falls back to the pending question
