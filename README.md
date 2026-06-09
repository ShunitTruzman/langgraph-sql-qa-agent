
# 🛒 LangGraph SQL QA Agent

A LangGraph-based Natural Language to SQL agent that answers questions over an E-commerce relational database.

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
git clone https://github.com/ShunitTruzman/langgraph-sql-qa-agent.git
cd langgraph-sql-qa-agent
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
```

### Windows (CMD Terminal)

```cmd
set OPENAI_API_KEY=your_openai_key  
set OPENAI_MODEL=gpt-4o-mini
```

The scripts read them using `os.getenv()`. Use a model that supports **Structured Outputs**
(`gpt-4o-mini` and `gpt-4o` both do).

---
## ▶️ Running the Project


```bash

python qa_agent.py
```
---
## 🧪 Running Tests

The suite (19 tests) covers DB joins, SQL generation, the retry loop, structured-output
wiring, and the full human-in-the-loop clarify → resume flow. Tests use a fake LLM, so
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


