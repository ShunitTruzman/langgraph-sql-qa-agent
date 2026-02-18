
# 🎓 LangGraph SQL QA Agent

A LangGraph-based Natural Language to SQL (NL2SQL) agent that answers questions over a University relational database.

---

## 🚀 System Overview

This system translates natural language questions into SQL queries, executes them against a relational database, and returns a clear, human-readable answer.

The execution flow is:

User question → load schema → generate SQL (LLM) → execute SQL → answer (LLM) → Final Answer
                         └──────────── retry (on error) ────────────┘

The flow is implemented as a LangGraph state machine, where each step is a dedicated node.
This makes the system modular, traceable, and easy to debug.

---

## 🏗 Architecture

### Graph Nodes

- load_schema – Dynamically retrieves the database schema  
- gen_sql – Generates SQL or clarification requests using the LLM  
- exec_sql – Executes SQL safely  
- answer – Produces a final natural language response  

The system supports:
- Retry loop on SQL execution error  
- Clarification path for ambiguous questions  

---

## 🗄 Database Schema

Core entities:

- teachers  
- students  
- courses  
- course_offerings  
- enrollments  

The schema supports:

- Multi-table joins  
- Aggregations (AVG, COUNT)  
- Filtering by semester, year, teacher, or student  
- Grade constraints (0–100)  
- Unique enrollment enforcement  

---

# 5. 📊 Example Queries and Outputs

### Example 1 – Join Query

Input:

Who taught CS101 in Spring 2026?

Generated SQL:

SELECT t.name
FROM teachers t
JOIN course_offerings o ON t.id = o.teacher_id
JOIN courses c ON o.course_id = c.id
WHERE c.code = 'CS101'
AND o.semester = 'Spring'
AND o.year = 2026
LIMIT 10;

Output:

Dr. Alice Nguyen taught CS101 in Spring 2026.

---

### Example 2 – Aggregation

Input:

What is the average grade in CS101 in Spring 2026?

Generated SQL:

SELECT AVG(e.grade)
FROM enrollments e
JOIN course_offerings o ON e.offering_id = o.id
JOIN courses c ON o.course_id = c.id
WHERE c.code = 'CS101'
AND o.semester = 'Spring'
AND o.year = 2026
LIMIT 10;

Output:

The average grade is 90.5.

---

# 6. 🔍 Execution Traces Demonstrating the System Flow

## Successful Flow

[load_schema] Schema loaded (5 tables)  
[gen_sql] Generated SQL query  
[exec_sql] rows=1  
[answer] "Dr. Alice Nguyen taught CS101 in Spring 2026."

---

## Retry Flow (Error Recovery Example)

User: Who taught CS101 in 2026?

[gen_sql] SELECT teacher FROM ...  
[exec_sql] ERROR: no such column 'teacher'  

Retry triggered  

[gen_sql] SELECT t.name FROM teachers t ...  
[exec_sql] rows=1  
[answer] "Dr. Alice Nguyen taught CS101 in 2026."

---

## 🧪 Running Tests

pytest

---

## ▶️ Running the Project

pip install -r requirements.txt  
python langgraph_university_qa_Cloude2.py

---

## 🏭 Production Considerations (Summary)

- Add connection pooling  
- Add LLM retry with exponential backoff  
- Enforce SELECT-only at DB user level  
- Use structured JSON logging  
- Monitor SQL error rate and LLM latency  
- Containerize with Docker  
- Add CI pipeline (pytest)  
- Optionally replace custom tracing with LangSmith  

