
# 🎓 LangGraph QA Agent – University Database

A Question Answering system built with **LangGraph + SQL** that translates natural language questions into SQL queries over a university database.

---

## 🚀 Project Overview

This project implements a LangGraph-based QA agent that allows users to ask natural language questions such as:

> “Who taught CS101 in Spring 2026?”  
> “What is the average grade in Math 201?”

The system:

1. Loads the database schema dynamically  
2. Generates SQL using an LLM  
3. Executes the SQL query  
4. Returns a human-readable answer  
5. Logs a full execution trace  

---

## 🏗 System Architecture

```
User Question
    ↓
load_schema
    ↓
gen_sql (LLM)
    ↓
exec_sql
    ↓
answer (LLM)
    ↓
Final Answer
```

On SQL failure:

```
exec_sql → error → retry → gen_sql (with error feedback)
```

The system is implemented as a **LangGraph state machine**, ensuring modularity and observability.

---

## 🗄 Database Schema

Core entities:

- `teachers`
- `students`
- `courses`
- `course_offerings`
- `enrollments`

The schema supports:

- Joins across multiple tables
- Aggregations (AVG, COUNT)
- Filtering by semester, year, teacher, or student
- Grade constraints (0–100)
- Unique enrollment enforcement

See `university_schema_and_seed.sql`.

---

## 🧠 Key Design Features

### ✅ Database-Agnostic

The system dynamically extracts `CREATE TABLE` definitions and injects them into the LLM prompt.

No table names are hardcoded in the agent logic.

---

### ✅ Structured SQL Generation

The LLM must return strict JSON:

```json
{"type": "sql", "sql": "...", "params": {}}
```

or

```json
{"type": "clarify", "question": "..."}
```

Safety features:

- SELECT-only enforcement
- Automatic LIMIT injection
- Parameterized query support

---

### ✅ Full Execution Tracing

Each node logs:

- Timestamp
- Node name
- Generated SQL
- Query results
- Errors

Trace example:

```
[2026-02-15T14:02:01] load_schema
[2026-02-15T14:02:01] gen_sql → SELECT ...
[2026-02-15T14:02:02] exec_sql → rows=1
[2026-02-15T14:02:02] answer → "Dr. Alice Nguyen taught CS101 in Spring 2026."
```

---

## 📊 Example Queries and Outputs

### Example 1 – Join Query

**Input:**

Who taught CS101 in Spring 2026?

**Generated SQL:**

```sql
SELECT t.name
FROM teachers t
JOIN course_offerings o ON t.id = o.teacher_id
JOIN courses c ON o.course_id = c.id
WHERE c.code = 'CS101'
AND o.semester = 'Spring'
AND o.year = 2026
LIMIT 10;
```

**Output:**

Dr. Alice Nguyen taught CS101 in Spring 2026.

---

### Example 2 – Aggregation

**Input:**

What is the average grade in CS101 in Spring 2026?

**Generated SQL:**

```sql
SELECT AVG(e.grade)
FROM enrollments e
JOIN course_offerings o ON e.offering_id = o.id
JOIN courses c ON o.course_id = c.id
WHERE c.code = 'CS101'
AND o.semester = 'Spring'
AND o.year = 2026
LIMIT 10;
```

**Output:**

The average grade is 90.5.

---

## 🧪 Testing

Run tests:

```bash
pytest
```

The project includes:

- Database constraint tests
- SQL generation tests
- Retry logic tests
- End-to-end agent tests
- Trace validation tests

Mock LLMs ensure deterministic behavior.

---

## ▶️ Running the Project

```bash
pip install -r requirements.txt
python langgraph_university_qa_Cloude2.py
```

---

## 🏭 Production Considerations

To move this system into production:

- Add connection pooling
- Add LLM retry with exponential backoff
- Enforce SELECT-only at DB user level
- Use structured JSON logs
- Add monitoring for error rates and latency
- Containerize with Docker
- Add CI pipeline with pytest
- Optionally replace custom tracing with LangSmith

The stateless design enables horizontal scaling.

---

## 📁 Project Structure

```
.
├── langgraph_university_qa_Cloude2.py
├── university_schema_and_seed.sql
├── test_agent.py
├── README.md
```

---

## 🎯 Evaluation Goals

This project demonstrates:

- Correctness
- Clear architecture
- Observability
- Robust error handling
- Production awareness
- Clean modular design

---

## 👩‍💻 Author

Your Name
