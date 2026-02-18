
# 🎓 LangGraph SQL QA Agent

A LangGraph-based Natural Language to SQL agent that answers questions over a University relational database.

---

## 🚀 System Overview

This system translates natural language questions into SQL queries, executes them against a relational database, and returns a clear, human-readable answer.

The execution flow is:

User question → load schema → generate SQL (LLM) → execute SQL → answer (LLM) → Final Answer  
.....................................................................└─── retry (on error) ─────┘........................................................................

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
-🔁 Retry loop on SQL execution error   
-❓ Clarification path for ambiguous questions  

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

The scripts read them using `os.getenv()`.

---
## ▶️ Running the Project


```bash

python qa_agent.py
```
---
## 🧪 Running Tests

```bash

pytest
```
---
#  📊 Example Queries and Outputs
Below are real examples from a run of the system.  
For each question, we show: Input → Generated SQL → Final Answer.

### Example 1 – Multi-step Query (Join + Aggregation)

**Input:**  
Which teacher taught CS101 in Spring 2026 and what was the average grade?

**Generated SQL(example):**  
SELECT t.name AS teacher_name, AVG(e.grade) AS average_grade  
FROM course_offerings co  
JOIN courses c ON co.course_id = c.course_id  
JOIN teachers t ON co.teacher_id = t.teacher_id  
LEFT JOIN enrollments e ON co.offering_id = e.offering_id  
WHERE c.code = 'CS101'  
  AND co.semester = 'Spring' 
  AND co.year = 2026  
GROUP BY t.name  
LIMIT 50;

**Output:**  
Dr. Alice Nguyen taught CS101 in Spring 2026, and the average grade was 90.5.

---

### Example 2 – Join Query

**Input:**  
Who taught CS101 in Spring 2026?  

**Generated SQL(example):**    
SELECT t.name  
FROM course_offerings co  
JOIN courses c ON co.course_id = c.course_id  
JOIN teachers t ON co.teacher_id = t.teacher_id  
WHERE c.code = 'CS101'  
  AND co.semester = 'Spring'  
  AND co.year = 2026  
LIMIT 50;

**Output:**  
Dr. Alice Nguyen taught CS101 in Spring 2026.

---

### Example 3 – Aggregation

**Input:**  
What is the average grade in CS101 Spring 2026?

**Generated SQL(example):**  
SELECT AVG(e.grade) AS average_grade  
FROM enrollments e  
JOIN course_offerings co ON e.offering_id = co.offering_id  
JOIN courses c ON co.course_id = c.course_id  
WHERE c.code = 'CS101'  
  AND co.semester = 'Spring'  
  AND co.year = 2026  
LIMIT 50;

**Output:**
The average grade in CS101 for Spring 2026 is 90.5.

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
[answer] {"answer": "Dr. Alice Nguyen taught CS101 in Spring 2026, and the average grade was 90.5."}

---


