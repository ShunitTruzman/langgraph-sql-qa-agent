# 🛒 LangGraph SQL RAG Agent

A LangGraph-based agent that turns natural-language questions into SQL over a relational database — featuring retrieval (RAG) over the schema, self-correcting queries, human-in-the-loop clarification, and full execution tracing via LangSmith for observability and debugging.

---

## 🚀 System Overview

This system translates natural language questions into SQL queries, executes them against a relational database, and returns a clear, human-readable answer.

The execution flow is:

User question → load schema → generate SQL (LLM) → execute SQL → answer (LLM) → Final Answer  
.....................................................................└─── retry (on error) ─────┘........................................................................

If the question is ambiguous, the agent **pauses and asks the user a clarifying question**
(human-in-the-loop), then resumes with the user's reply folded into the question.

The flow is implemented as a LangGraph state machine, where each step is a dedicated node.  
This makes the system modular, traceable, and easy to debug.

---

## 🏗 Architecture

### Graph Nodes

- load_schema – Dynamically retrieves the database schema  
- gen_sql – Generates SQL or clarification requests using the LLM (via **structured outputs**)  
- clarify – Pauses the graph and asks the user for missing detail (**human-in-the-loop**)  
- exec_sql – Executes SQL safely  
- answer – Produces a final natural language response  

The system supports:  
-🔁 Retry loop on SQL execution error (the error is fed back to the LLM to self-correct)   
-❓ Human-in-the-loop clarification for ambiguous questions, bounded by `max_clarifications`  
-🧱 Structured outputs — the LLM's SQL/clarify decision is guaranteed-valid JSON (no fragile parsing)  
-🔒 Safety: only `SELECT` queries are allowed, and a `LIMIT` is enforced  

### Structured outputs

`gen_sql` calls the LLM with a strict JSON schema (`SQL_DECISION_SCHEMA`), so the response is
always valid JSON of the form `{"type": "sql"|"clarify", "sql": ..., "question": ...}`. This
removes the need to "hope" the model returns clean JSON.

### Human-in-the-loop clarification

When the question is ambiguous, `gen_sql` returns `type="clarify"`. The `clarify` node calls
LangGraph's `interrupt()`, which **suspends** the graph and surfaces the question to the caller.
The caller collects the user's reply and resumes the run with `Command(resume=reply)`; the reply
is appended to the question and the agent loops back to `gen_sql`. Resumability is backed by a
LangGraph checkpointer (`MemorySaver` by default).

In code, pass an `on_clarify` callback to `run_question` (use `on_clarify=input` for a CLI). If no
callback is given, the agent stays single-shot and simply returns the clarification question.

### Schema retrieval (RAG)

For large databases, dumping every table's DDL into the prompt is wasteful and noisy. Pass an
embedder (`embed=`) to enable **RAG over the schema**: each table's DDL is embedded once, and per
question the agent retrieves only the most relevant tables (cosine similarity), then **expands the
set along foreign keys** so multi-table JOINs still work. Without an embedder it falls back to the
full schema.

```python
from qa_agent import run_question, make_openai_llm, make_openai_embedder

answer, trace, _ = run_question(
    conn=conn,
    llm=make_openai_llm(),
    embed=make_openai_embedder(),   # enables schema retrieval
    question="Which customers from Israel spent the most?",
)
```

This makes the project a demonstration of **both** text-to-SQL *and* retrieval (RAG). On the tiny
demo schema (5 tables) the benefit is illustrative; the technique matters on schemas with dozens or
hundreds of tables, where it keeps the prompt small and focused.

### Observability & tracing (LangSmith)

Every run can be traced end-to-end with **LangSmith**, LangChain's observability platform. Because
the agent is built on LangGraph, basic tracing requires **no code changes** — set the environment
variables below and each run streams to your LangSmith dashboard, where you can inspect every node
(`load_schema`, `gen_sql`, `exec_sql`, ...), its inputs and outputs, latency, and the exact order of
calls, including the retry and clarify loops.

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_PROJECT=langgraph-sql-rag-agent   # optional: groups runs in the dashboard
```

The OpenAI calls inside the custom `llm` / `embed` wrappers can additionally be nested as LLM spans
(with prompts and token counts) by decorating them with `@traceable` from the `langsmith` package,
or by wrapping the OpenAI client with `wrap_openai`.

This complements the lightweight built-in `_trace` (shown at the end of this README): the in-process
trace is handy for quick local debugging and tests, while LangSmith gives full, persistent, visual
observability.

---

## 🗄 Database Schema

Core entities:

- customers  
- categories  
- products  
- orders  
- order_items  

The schema supports:

- Multi-table joins (orders → customers, order_items → products → categories)  
- Aggregations (SUM revenue, AVG price, COUNT orders)  
- Filtering by country, category, order status, or date  
- Price/quantity constraints (price ≥ 0, quantity > 0)  
- Order-status lifecycle enum and a unique product-per-order rule

---

## 🧩 Cloning & Running the Project

### Clone the repository

```bash
git clone https://github.com/ShunitTruzman/langgraph-sql-rag-agent.git
cd langgraph-sql-rag-agent
```

✔ Important: You **must be inside the project folder** to run the scripts.

---

## ⚙️ Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .\.venv\Scripts\Activate.ps1  # Windows PowerShell
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

---

## 🔑 Environment Variables

### macOS / Linux

```bash
export OPENAI_API_KEY="your_openai_key"
export OPENAI_MODEL="gpt-4o-mini"

# Optional — enable LangSmith tracing for observability/debugging
export LANGSMITH_TRACING="true"
export LANGSMITH_API_KEY="your_langsmith_key"
export LANGSMITH_PROJECT="langgraph-sql-rag-agent"
```

### Windows (CMD Terminal)

```cmd
set OPENAI_API_KEY=your_openai_key
set OPENAI_MODEL=gpt-4o-mini

:: Optional - enable LangSmith tracing for observability/debugging
set LANGSMITH_TRACING=true
set LANGSMITH_API_KEY=your_langsmith_key
set LANGSMITH_PROJECT=langgraph-sql-rag-agent
```

The scripts read them using `os.getenv()`. Use a model that supports **Structured Outputs**
(`gpt-4o-mini` and `gpt-4o` both do). The `LANGSMITH_*` variables are optional — set them only if
you want runs traced to LangSmith. (The older `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` /
`LANGCHAIN_PROJECT` names also work.)

---
## ▶️ Running the Project


```bash

python qa_agent.py
```
---
## 🧪 Running Tests

The suite (23 tests) covers DB joins, SQL generation, the retry loop, structured-output
wiring, the full human-in-the-loop clarify → resume flow, and schema retrieval (RAG). Tests use a fake LLM, so
**no API key is required** to run them.

```bash

pytest -v
```
---
#  📊 Example Queries and Outputs
Below are real examples from a run of the system.  
For each question, we show: Input → Generated SQL → Final Answer.

### Example 1 – Aggregation + Filter

**Input:**  
What is the total revenue from delivered orders?

**Generated SQL (example):**  
SELECT SUM(oi.quantity * oi.unit_price) AS total_revenue  
FROM order_items oi  
JOIN orders o ON o.order_id = oi.order_id  
WHERE o.status = 'delivered'  
LIMIT 50;

**Output:**  
The total revenue from delivered orders is 476.0.

---

### Example 2 – Join + Count

**Input:**  
How many orders has Alice Cohen placed?

**Generated SQL (example):**  
SELECT COUNT(*) AS order_count  
FROM orders o  
JOIN customers c ON c.customer_id = o.customer_id  
WHERE c.name = 'Alice Cohen'  
LIMIT 50;

**Output:**  
Alice Cohen has placed 3 orders.

---

### Example 3 – Top-N by Revenue

**Input:**  
Which 3 products generated the most revenue?

**Generated SQL (example):**  
SELECT p.name, SUM(oi.quantity * oi.unit_price) AS revenue  
FROM order_items oi  
JOIN products p ON p.product_id = oi.product_id  
GROUP BY p.product_id  
ORDER BY revenue DESC  
LIMIT 3;

**Output:**  
The top product by revenue is Wireless Headphones (240.0), followed by the next two best sellers.

---

# 🔍 Execution Traces Demonstrating the System Flow

The system records a full trace of the run:  
User Input → LangGraph Nodes → SQL → DB Results → Final Answer

Below is a real trace example (shortened):  
[load_schema] {"chars": 1246}  
[attempt] {"n": 1}  
[llm_raw] {"raw": "...JSON..."}  
[gen_sql] {"sql": "SELECT ... LIMIT 50"}  
[exec_sql] {"rows": 1}  
[answer] {"answer": "The total revenue from delivered orders is 476.0."}

---