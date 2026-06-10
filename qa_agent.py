"""
LangGraph QA Agent over a SQL e-commerce database.

Flow: NL question → load schema → generate SQL (LLM) → execute SQL → generate answer (LLM)
If SQL execution fails, the agent retries up to `max_attempts` times, passing the error
back to the LLM so it can self-correct.

If the question is ambiguous, the agent pauses (LangGraph `interrupt`) and asks the user
a clarifying question. The user's reply is folded back into the question and the agent
resumes — a true human-in-the-loop loop, bounded by `max_clarifications`.

DB-agnostic: all schema knowledge is discovered at runtime via introspection (_schema_text).
Structured output: SQL generation uses the LLM's JSON-schema structured-output mode, so the
decision is guaranteed to be valid JSON matching a fixed schema (no fragile prompt-only JSON).
Tracing: every node appends a timestamped event to state["trace"] for full observability.
"""

from __future__ import annotations
import os
import json, sqlite3, time, textwrap, uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import MemorySaver
from openai import OpenAI
from dotenv import load_dotenv

from schema_retrieval import SchemaIndex

load_dotenv()

# ── Types ──────────────────────────────────────────────────────────────────────

class QAState(TypedDict, total=False):
    """
    Shared state passed between every LangGraph node.
    Each node reads what it needs and writes its outputs back into this dict.
    """
    question: str               # The original user question (augmented on clarification)
    schema: str                 # DB schema text injected into LLM prompts
    decision: str               # "sql" → run a query | "clarify" → ask user for more info
    clarification_question: str # Question to ask the user when decision == "clarify"
    sql: str                    # Generated SQL SELECT statement
    attempts: int               # Number of SQL generation attempts so far (retry budget)
    clarifications: int         # Number of clarification rounds so far (clarify budget)
    last_error: str             # Most recent error message (empty string = no error)
    columns: List[str]          # Column names returned by the SQL query
    rows: List[List[Any]]       # Row data returned by the SQL query
    answer: str                 # Final human-readable answer shown to the user
    trace: List[Dict]           # Ordered list of trace events for observability


# ── Structured-output schema ────────────────────────────────────────────────────
# The LLM must return JSON matching this exact schema when generating SQL.
# Using strict structured outputs means the response is *guaranteed* to be valid
# JSON in this shape — no markdown fences, no prose, no parsing guesswork.
SQL_DECISION_SCHEMA: Dict[str, Any] = {
    "name": "sql_decision",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["sql", "clarify"],
                "description": "'sql' to run a query, 'clarify' to ask the user for more info.",
            },
            "sql": {
                "type": ["string", "null"],
                "description": "A single SQL SELECT query when type=='sql', otherwise null.",
            },
            "question": {
                "type": ["string", "null"],
                "description": "A clarifying question when type=='clarify', otherwise null.",
            },
        },
        "required": ["type", "sql", "question"],
        "additionalProperties": False,
    },
}


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
    - max_attempts:       how many times the agent may retry SQL generation after an error
                          (e.g. 2 = 1 initial attempt + 1 repair attempt)
    - max_rows:           safety cap appended as LIMIT if the LLM omits one
    - max_clarifications: how many clarification rounds are allowed before the agent
                          stops asking and answers with its best effort
    - schema_top_k:       when schema retrieval (RAG) is enabled, how many tables to retrieve
                          for the question before foreign-key expansion
    """
    max_attempts: int = 2
    max_rows: int = 50
    max_clarifications: int = 2
    schema_top_k: int = 3  # when schema-RAG is enabled, how many tables to retrieve


# ── Prompts ────────────────────────────────────────────────────────────────────

def _sql_prompt(question: str, schema: str, last_error: str = "") -> str:
    """
    Build the prompt sent to the LLM for SQL generation.
    - Injects the live DB schema so the LLM only references real tables/columns.
    - If last_error is set (retry path), appends the error so the LLM can self-correct.
    - The response *shape* is enforced by SQL_DECISION_SCHEMA (structured output),
      so the prompt only needs to describe intent, not formatting.
    """
    repair = f"\nPrevious SQL failed: {last_error}\nFix it.\n" if last_error else ""
    return textwrap.dedent(f"""
You are a careful data assistant. Convert the user question into a single SQL SELECT query.
Rules:
- Use ONLY tables/columns in the schema. Prefer explicit JOINs.
- If the question is ambiguous, ask for clarification instead of guessing
  (set type="clarify" and put your question in the "question" field).
- When you can answer, set type="sql" and put the query in the "sql" field.
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
    With structured outputs the response is already guaranteed valid JSON, so the
    direct parse path is the norm. The fallback (extract first {...} block) keeps
    the agent working with LLM backends that don't support structured outputs.
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
def build_app(*, conn: Any, llm: Callable[..., str], config: Optional[AgentConfig] = None,
              get_schema_text: Optional[Callable[[Any], str]] = None,
              embed: Optional[Callable[[List[str]], List[List[float]]]] = None,
              checkpointer: Optional[Any] = None):
    """
    Compile and return a LangGraph runnable.

    Schema handling:
        If `embed` is provided, the agent uses **schema RAG**: it embeds each table's DDL once
        and, per question, retrieves only the most relevant tables (then expands along foreign
        keys). If `embed` is None, it falls back to dumping the full schema (original behavior).

    Graph topology:
        START → load_schema → attempt → gen_sql ──(sql)──▶ exec_sql ──(no_retry)──▶ answer → END
                                  ▲          │                  │
                          (retry on error)   └──(clarify)──▶ clarify ──(resume)──┐
                                  │                                              │
                                  └────────────────────────────────────── loop back to gen_sql

    Args:
        conn:            Any DB connection with a .cursor() interface (SQLite used here).
        llm:             Callable (prompt, *, response_schema=None) -> str. Swap to change provider.
        config:          Optional AgentConfig for tuning retry/row/clarify/retrieval limits.
        get_schema_text: Optional full-schema introspection override (used when embed is None).
        embed:           Optional embedder Callable[[List[str]], List[List[float]]]; enables schema RAG.
        checkpointer:    LangGraph checkpointer (defaults to in-memory). Required for the
                         human-in-the-loop `interrupt`/resume to work.
    """
    cfg = config or AgentConfig()
    schema_fn = get_schema_text or _schema_text
    schema_index = SchemaIndex.build(conn, embed) if embed is not None else None

    def load_schema(state: QAState) -> QAState:
        """Node 1 — Make the schema available to the prompt.

        With an embedder, retrieve only the tables relevant to the question (schema RAG);
        otherwise dump the full schema.
        """
        if schema_index is not None:
            state["schema"] = schema_index.retrieve(state["question"], embed, top_k=cfg.schema_top_k)
            _trace(state, "load_schema", chars=len(state["schema"]), mode="retrieval",
                   top_k=cfg.schema_top_k)
        else:
            state["schema"] = schema_fn(conn)
            _trace(state, "load_schema", chars=len(state["schema"]), mode="full")
        return state

    def gen_sql(state: QAState) -> QAState:
        """
        Node 2 — Ask the LLM to generate SQL (or request clarification).
        - Builds the prompt with the current schema and any previous error (retry path).
        - Calls the LLM with a structured-output schema → guaranteed-valid JSON.
        - Validates that the query is a SELECT (rejects writes for safety).
        - Appends LIMIT if the LLM omitted it.
        - Sets state["decision"] = "sql" or "clarify" for downstream routing.
        """
        raw = llm(
            _sql_prompt(state["question"], state["schema"], state.get("last_error", "")),
            response_schema=SQL_DECISION_SCHEMA,
        )
        _trace(state, "llm_raw", raw=raw)

        try:
            obj = _parse_json(raw)
        except Exception as e:
            # Unparseable output — mark as error so the retry loop triggers.
            state.update(decision="sql", sql="", last_error=f"Invalid JSON: {e}")
            return state

        if obj.get("type") == "clarify":
            # The question is too ambiguous to answer — ask the user.
            state.update(decision="clarify",
                         clarification_question=obj.get("question") or "Can you clarify?")
            return state

        sql = (obj.get("sql") or "").strip().rstrip(";")
        if not sql.lower().startswith("select"):
            # Reject any non-SELECT query for security (no INSERT/UPDATE/DROP etc.)
            state.update(decision="sql", sql="", last_error="Only SELECT allowed.")
            return state

        if " limit " not in sql.lower():
            sql += f" LIMIT {cfg.max_rows}"  # Prevent runaway large result sets

        state.update(decision="sql", sql=sql, last_error="")
        _trace(state, "gen_sql", sql=sql)
        return state

    def clarify(state: QAState) -> QAState:
        """
        Node 2b (human-in-the-loop) — Pause the graph and ask the user to clarify.

        `interrupt(...)` suspends execution and surfaces the clarification question to
        the caller. When the caller resumes with the user's reply (Command(resume=...)),
        the node re-runs and `interrupt(...)` returns that reply. We fold the reply into
        the question and loop back to gen_sql to try again — bounded by max_clarifications.
        """
        reply = interrupt({"clarification_question": state.get("clarification_question", "Can you clarify?")})
        state["clarifications"] = state.get("clarifications", 0) + 1
        state["question"] = f'{state["question"]}\n\nAdditional context from user: {reply}'
        state["decision"] = ""
        state["clarification_question"] = ""
        _trace(state, "clarify", reply=reply, n=state["clarifications"])
        return state

    def exec_sql(state: QAState) -> QAState:
        """
        Node 3 — Execute the generated SQL against the database.
        - On success: stores columns and rows in state, clears last_error.
        - On failure: stores the error message in last_error so the retry loop
          can pass it back to the LLM for self-correction.
        """
        sql = state.get("sql", "")
        if not sql:
            state["last_error"] = state.get("last_error") or "No SQL produced."
            return state
        cur = conn.cursor()
        try:
            cur.execute(sql)
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
          1. decision == "clarify" (clarify budget exhausted): return the pending question.
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
        Node 5 — Increment the SQL-attempt counter before each generation call on the
        initial/retry path. Clarification loops bypass this node, so they don't consume
        the SQL retry budget.
        """
        state["attempts"] = state.get("attempts", 0) + 1
        _trace(state, "attempt", n=state["attempts"])
        return state

    def route_after_gen(state: QAState) -> str:
        """
        Conditional edge after gen_sql.
        - "clarify"  → pause for the user (while clarify budget remains)
        - "answer"   → clarify budget exhausted; answer with the pending question
        - "exec_sql" → run the generated SQL
        """
        if state.get("decision") == "clarify":
            if state.get("clarifications", 0) < cfg.max_clarifications:
                return "clarify"
            return "answer"
        return "exec_sql"

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
                     ("gen_sql", gen_sql), ("clarify", clarify),
                     ("exec_sql", exec_sql), ("answer", answer)]:
        g.add_node(name, fn)

    g.add_edge(START, "load_schema")
    g.add_edge("load_schema", "attempt")
    g.add_edge("attempt", "gen_sql")
    g.add_conditional_edges("gen_sql", route_after_gen,
                            {"clarify": "clarify", "exec_sql": "exec_sql", "answer": "answer"})
    g.add_edge("clarify", "gen_sql")
    g.add_conditional_edges("exec_sql", should_retry, {"retry": "attempt", "no_retry": "answer"})
    g.add_edge("answer", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


# ── Public API ─────────────────────────────────────────────────────────────────

def run_question(*, conn, llm, question: str,
                 config: Optional[AgentConfig] = None,
                 on_clarify: Optional[Callable[[str], str]] = None,
                 embed: Optional[Callable[[List[str]], List[List[float]]]] = None,
                 thread_id: Optional[str] = None) -> Tuple[str, List, QAState]:
    """
    Convenience wrapper: build the app, run one question, return results.

    Schema RAG:
        Pass `embed` to enable schema retrieval (only relevant tables go into the prompt).
        Omit it to use the full schema.

    Human-in-the-loop:
        If `on_clarify` is provided, it is called with the clarification question and must
        return the user's reply (str); the agent then resumes automatically. In a CLI you
        can pass `on_clarify=input`. If `on_clarify` is None, the agent does not block —
        it returns the clarification question as the answer (single-shot behavior).

    Returns:
        answer (str)          — final human-readable answer (or clarification question)
        trace  (List[Dict])   — full execution trace for debugging
        state  (QAState)      — complete final state (includes SQL, rows, etc.)
    """
    app = build_app(conn=conn, llm=llm, config=config, get_schema_text=_schema_text, embed=embed)
    run_config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}
    state: QAState = {"question": question, "trace": [], "attempts": 0, "clarifications": 0}

    out = app.invoke(state, config=run_config)

    # Drive the human-in-the-loop clarification loop, if the graph paused.
    while "__interrupt__" in out:
        intr = out["__interrupt__"][0]
        payload = getattr(intr, "value", intr)
        clar_q = payload.get("clarification_question", "Can you clarify?") \
            if isinstance(payload, dict) else str(payload)

        if on_clarify is None:
            # Non-interactive: surface the question instead of blocking.
            return clar_q, out.get("trace", []), out

        reply = on_clarify(clar_q)
        out = app.invoke(Command(resume=reply), config=run_config)

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

def make_openai_llm(model: str = "gpt-4o-mini") -> Callable[..., str]:
    """
    Returns a Callable (prompt, *, response_schema=None) -> str backed by OpenAI.
    Reads OPENAI_API_KEY from .env via load_dotenv().
    temperature=0 → deterministic SQL generation (no creativity, just accuracy).

    When `response_schema` is provided, OpenAI Structured Outputs are used, guaranteeing
    the returned content is valid JSON conforming to that schema.
    """
    client = OpenAI()
    def llm(prompt: str, *, response_schema: Optional[Dict[str, Any]] = None) -> str:
        kwargs: Dict[str, Any] = {}
        if response_schema is not None:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": response_schema}
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            **kwargs,
        )
        return response.choices[0].message.content
    return llm


def make_openai_embedder(model: str = "text-embedding-3-small") -> Callable[[List[str]], List[List[float]]]:
    """
    Returns an embedder Callable[[List[str]], List[List[float]]] backed by OpenAI.
    Pass it to build_app/run_question to enable schema retrieval (RAG over the schema).
    """
    client = OpenAI()
    def embed(texts: List[str]) -> List[List[float]]:
        resp = client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in resp.data]
    return embed


if __name__ == "__main__":
    conn = load_sql_file(
        os.path.join(os.path.dirname(__file__),
                     "ecommerce_schema_and_seed.sql")
    )

    llm = make_openai_llm(model="gpt-4o-mini")
    embed = make_openai_embedder()  # enables schema RAG (retrieve relevant tables per question)

    questions = [
        "How many orders has Alice Cohen placed?",
        "What is the total revenue from delivered orders?",
        "Which 3 products generated the most revenue?",
        "What is the average price of products in the Electronics category?",
        "Tell me about products",          # triggers clarify path (interactive)
        "List customers from Israel and how much each has spent.",
    ]

    for q in questions:
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        # In the CLI, answer clarification prompts interactively via input().
        ans, trace, _ = run_question(conn=conn, llm=llm, question=q, on_clarify=input, embed=embed)
        print(f"A: {ans}")
        print("\n--- TRACE ---")
        print(format_trace(trace))
