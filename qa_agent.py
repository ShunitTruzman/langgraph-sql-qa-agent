"""
LangGraph QA Agent over a SQL university database.

Flow: NL question → load schema → generate SQL (LLM) → execute SQL → generate answer (LLM)
If SQL execution fails, the agent retries up to `max_attempts` times, passing the error
back to the LLM so it can self-correct.

DB-agnostic: all schema knowledge is discovered at runtime via introspection (_schema_text).
Tracing: every node appends a timestamped event to state["trace"] for full observability.
"""

from __future__ import annotations
import os
import json, sqlite3, time, textwrap
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict
from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── Types ──────────────────────────────────────────────────────────────────────

class QAState(TypedDict, total=False):
    """
    Shared state passed between every LangGraph node.
    Each node reads what it needs and writes its outputs back into this dict.
    """
    question: str               # The original user question (never mutated)
    schema: str                 # DB schema text injected into LLM prompts
    decision: str               # "sql" → run a query | "clarify" → ask user for more info
    clarification_question: str # Question to ask the user when decision == "clarify"
    sql: str                    # Generated SQL SELECT statement
    sql_params: Dict[str, Any]  # Optional named parameters for the SQL query
    attempts: int               # Number of SQL generation attempts so far
    last_error: str             # Most recent error message (empty string = no error)
    columns: List[str]          # Column names returned by the SQL query
    rows: List[List[Any]]       # Row data returned by the SQL query
    answer: str                 # Final human-readable answer shown to the user
    trace: List[Dict]           # Ordered list of trace events for observability


# ── Tracing ────────────────────────────────────────────────────────────────────

def _trace(state: QAState, node: str, **data) -> None:
    """
    Append a trace event to state["trace"].
    Each event records the current timestamp (ms), the node name, and any
    keyword arguments as structured data — making the full run inspectable.
    """
    state.setdefault("trace", []).append(
        {"ts_ms": int(time.time() * 1000), "node": node, "data": data}
    )

def format_trace(trace: List[Dict]) -> str:
    """
    Convert a list of trace events into a human-readable string.
    Useful for debugging during interviews or printing to console.
    Format: [timestamp_ms] node_name  \n  {json payload}
    """
    return "\n\n".join(
        f"[{e['ts_ms']}] {e['node']}\n{json.dumps(e['data'], ensure_ascii=False, indent=2)}"
        for e in trace
    )


# ── Config ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentConfig:
    """
    Immutable runtime configuration for the agent.
    - max_attempts: how many times the agent may retry SQL generation after an error
                    (e.g. 2 = 1 initial attempt + 1 repair attempt)
    - max_rows:     safety cap appended as LIMIT if the LLM omits one
    """
    max_attempts: int = 2
    max_rows: int = 50


# ── Prompts ────────────────────────────────────────────────────────────────────

def _sql_prompt(question: str, schema: str, last_error: str = "") -> str:
    """
    Build the prompt sent to the LLM for SQL generation.
    - Injects the live DB schema so the LLM only references real tables/columns.
    - If last_error is set (retry path), appends the error so the LLM can self-correct.
    - Instructs the LLM to return STRICT JSON (no mark down) with either:
        {"type":"sql","sql":"...","params":{}}   — a runnable SELECT query
        {"type":"clarify","question":"..."}      — a clarification request
    """
    repair = f"\nPrevious SQL failed: {last_error}\nFix it.\n" if last_error else ""
    return textwrap.dedent(f"""
You are a careful data assistant. Convert the user question into a single SQL SELECT query.
Rules:
- Use ONLY tables/columns in the schema. Prefer explicit JOINs.
- If the question is ambiguous, ask for clarification instead of guessing.
- Return STRICT JSON only (no markdown):
  {{"type":"sql","sql":"...","params":{{}}}}  or  {{"type":"clarify","question":"..."}}
Schema:
{schema}
User question: {question}{repair}
""").strip()

def _answer_prompt(question: str, sql: str, columns: List, rows: List) -> str:
    """
    Build the prompt sent to the LLM for answer generation.
    - Provides the original question, the SQL that was run, and its results.
    - Instructs the LLM to stay grounded in the data (no hallucination).
    - If results are empty, the LLM should say so and suggest a likely reason.
    """
    return textwrap.dedent(f"""
You are a helpful assistant. Write a concise, human-readable answer grounded ONLY in the SQL result.
If empty, say so and suggest a likely reason.
User question: {question}
SQL: {sql}
Result: {json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False)}
""").strip()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _schema_text(conn: sqlite3.Connection) -> str:
    """
    Introspect the SQLite database and return all CREATE TABLE statements as a
    single string. This is the DB-agnostic schema discovery mechanism — the agent
    never has hardcoded table names; it always reads them at runtime.
    """
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return "\n\n".join(row[0].strip() + ";" for row in cur.fetchall() if row[0])

def _parse_json(text: str) -> Dict:
    """
    Robustly parse JSON from LLM output.
    First tries a direct parse; if that fails, extracts the first {...} block
    to handle cases where the LLM wraps JSON in markdown or adds extra text.
    Raises if no valid JSON object can be found.
    """
    s = text.strip()
    try:
        return json.loads(s)
    except Exception:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start:end + 1])
        raise


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_app(*, conn: Any, llm: Callable[[str], str], config: Optional[AgentConfig] = None):
    """
    Compile and return a LangGraph runnable.

    Graph topology:
        START → load_schema → attempt → gen_sql → exec_sql
                                  ↑                   |
                                  └── (retry) ────────┘
                                                      |
                                               (no_retry) → answer → END

    Args:
        conn:   Any DB connection with a .cursor() interface (SQLite used here).
        llm:    Callable (prompt: str) -> str. Swap this to change the LLM provider.
        config: Optional AgentConfig for tuning retry/row limits.
    """
    cfg = config or AgentConfig()

    def load_schema(state: QAState) -> QAState:
        """Node 1 — Discover the DB schema and store it in state for use in prompts."""
        state["schema"] = _schema_text(conn)
        _trace(state, "load_schema", chars=len(state["schema"]))
        return state

    def gen_sql(state: QAState) -> QAState:
        """
        Node 2 — Ask the LLM to generate SQL (or request clarification).
        - Builds the prompt with the current schema and any previous error (retry path).
        - Parses the LLM's JSON response.
        - Validates that the query is a SELECT (rejects writes for safety).
        - Appends LIMIT if the LLM omitted it.
        - Sets state["decision"] = "sql" or "clarify" for downstream routing.
        """
        raw = llm(_sql_prompt(state["question"], state["schema"], state.get("last_error", "")))
        _trace(state, "llm_raw", raw=raw)

        try:
            obj = _parse_json(raw)
        except Exception as e:
            # LLM returned unparseable output — mark as error so retry loop triggers
            state.update(decision="sql", sql="", sql_params={}, last_error=f"Invalid JSON: {e}")
            return state

        if obj.get("type") == "clarify":
            # LLM decided the question is too ambiguous to answer — ask the user
            state.update(decision="clarify",
                         clarification_question=obj.get("question", "Can you clarify?"))
            return state

        sql = obj.get("sql", "").strip().rstrip(";")
        if not sql.lower().startswith("select"):
            # Reject any non-SELECT query for security (no INSERT/UPDATE/DROP etc.)
            state.update(decision="sql", sql="", sql_params={}, last_error="Only SELECT allowed.")
            return state

        if " limit " not in sql.lower():
            sql += f" LIMIT {cfg.max_rows}"  # Prevent runaway large result sets

        state.update(decision="sql", sql=sql,
                     sql_params=obj.get("params") or {}, last_error="")
        _trace(state, "gen_sql", sql=sql)
        return state

    def exec_sql(state: QAState) -> QAState:
        """
        Node 3 — Execute the generated SQL against the database.
        - On success: stores columns and rows in state, clears last_error.
        - On failure: stores the error message in last_error so the retry loop
          can pass it back to the LLM for self-correction.
        """
        sql, params = state.get("sql", ""), state.get("sql_params") or {}
        if not sql:
            state["last_error"] = state.get("last_error") or "No SQL produced."
            return state
        cur = conn.cursor()
        try:
            cur.execute(sql, params) if params else cur.execute(sql)
            state["rows"] = [list(r) for r in cur.fetchall()]
            state["columns"] = [d[0] for d in (cur.description or [])]
            state["last_error"] = ""
            _trace(state, "exec_sql", rows=len(state["rows"]))
        except Exception as e:
            state.update(columns=[], rows=[], last_error=str(e))
            _trace(state, "exec_sql_error", error=str(e))
        finally:
            cur.close()
        return state

    def answer(state: QAState) -> QAState:
        """
        Node 4 (terminal) — Produce the final answer shown to the user.
        Three cases:
          1. decision == "clarify": return the clarification question as the answer.
          2. last_error is set (retries exhausted): return a debug-friendly error message.
          3. Normal path: ask the LLM to summarize the SQL results in plain English.
        """
        if state.get("decision") == "clarify":
            state["answer"] = state.get("clarification_question", "Can you clarify?")
        elif state.get("last_error"):
            state["answer"] = (f"Couldn't run a valid SQL query.\nError: {state['last_error']}\n"
                               "Try rephrasing or adding missing details.")
        else:
            state["answer"] = llm(_answer_prompt(
                state["question"], state["sql"],
                state.get("columns", []), state.get("rows", [])
            )).strip()
        _trace(state, "answer", answer=state["answer"])
        return state

    def inc_attempts(state: QAState) -> QAState:
        """
        Node 5 — Increment the attempt counter before each SQL generation call.
        Checked by should_retry() to enforce the max_attempts limit.
        """
        state["attempts"] = state.get("attempts", 0) + 1
        _trace(state, "attempt", n=state["attempts"])
        return state

    def should_retry(state: QAState) -> str:
        """
        Conditional edge after exec_sql.
        Returns "retry"    → loop back to inc_attempts → gen_sql (LLM self-correction)
        Returns "no_retry" → proceed to answer node
        Retry only happens when: there's an error AND we haven't exceeded max_attempts.
        """
        if state.get("decision") != "clarify" and state.get("last_error"):
            if state.get("attempts", 0) < cfg.max_attempts:
                return "retry"
        return "no_retry"

    # Wire up the graph nodes and edges
    g = StateGraph(QAState)
    for name, fn in [("load_schema", load_schema), ("attempt", inc_attempts),
                     ("gen_sql", gen_sql), ("exec_sql", exec_sql), ("answer", answer)]:
        g.add_node(name, fn)

    g.add_edge(START, "load_schema")
    g.add_edge("load_schema", "attempt")
    g.add_edge("attempt", "gen_sql")
    g.add_edge("gen_sql", "exec_sql")
    g.add_conditional_edges("exec_sql", should_retry, {"retry": "attempt", "no_retry": "answer"})
    g.add_edge("answer", END)
    return g.compile()


# ── Public API ─────────────────────────────────────────────────────────────────

def run_question(*, conn, llm, question: str,
                 config: Optional[AgentConfig] = None) -> Tuple[str, List, QAState]:
    """
    Convenience wrapper: build the app, run one question, return results.

    Returns:
        answer (str)          — final human-readable answer
        trace  (List[Dict])   — full execution trace for debugging
        state  (QAState)      — complete final state (includes SQL, rows, etc.)
    """
    app = build_app(conn=conn, llm=llm, config=config)
    state: QAState = {"question": question, "trace": [], "attempts": 0}
    out = app.invoke(state)
    return out.get("answer", ""), out.get("trace", []), out

def load_sql_file(sql_path: str, *, sqlite_path: str = ":memory:") -> sqlite3.Connection:
    """
    Load a .sql file into a SQLite database and return the connection.
    Defaults to an in-memory DB (":memory:") — pass a file path for persistence.
    Foreign key enforcement is enabled automatically.
    """
    conn = sqlite3.connect(sqlite_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    with open(sql_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn

def make_openai_llm(model: str = "gpt-4o-mini") -> Callable[[str], str]:
    """
    Returns a Callable[[str], str] backed by OpenAI.
    Reads OPENAI_API_KEY from .env via load_dotenv().
    temperature=0 → deterministic SQL generation (no creativity, just accuracy).
    """
    client = OpenAI()
    def llm(prompt: str) -> str:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return response.choices[0].message.content
    return llm


if __name__ == "__main__":
    conn = load_sql_file(
        os.path.join(os.path.dirname(__file__),
                     "university_schema_and_seed.sql")
    )

    llm = make_openai_llm(model="gpt-4o-mini")

    questions = [
        "Which teacher taught CS101 in Spring 2026 and what was the average grade?",
        "Who taught CS101 in Spring 2026?",
        "What was the average grade in CS101 Spring 2026?",
        "How many courses is Maya Patel enrolled in?",
        "Tell me about courses",          # triggers clarify path
        "Which students passed CS201 in Fall 2025 with a grade above 90?",
    ]

    for q in questions:
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        ans, trace, _ = run_question(conn=conn, llm=llm, question=q)
        print(f"A: {ans}")
        print("\n--- TRACE ---")
        print(format_trace(trace))
