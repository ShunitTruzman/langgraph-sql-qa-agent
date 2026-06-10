"""
RAG over the database schema.

Instead of dumping every table's DDL into the prompt, we embed each table's schema text
once, and at query time retrieve only the tables most relevant to the user's question
(by cosine similarity), then expand that set along foreign keys so multi-table JOINs still
work. On a huge schema this keeps the prompt small and focused; on a tiny one it gracefully
returns everything.

The embedder is injected as a `Callable[[List[str]], List[List[float]]]` (mirroring how the
LLM is injected elsewhere), so the whole thing is testable with a fake, deterministic embedder
and needs no network.
"""

from __future__ import annotations
import math
from typing import Callable, Dict, List

EmbedFn = Callable[[List[str]], List[List[float]]]


def table_schemas(conn) -> Dict[str, str]:
    """Return {table_name: 'CREATE TABLE ...;'} for every user table, via introspection."""
    cur = conn.cursor()
    cur.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return {name: sql.strip() + ";" for name, sql in cur.fetchall() if sql}


def _fk_referenced_tables(conn, table: str) -> List[str]:
    """Tables that `table` points at via foreign keys (one hop)."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA foreign_key_list({table})")  # table name comes from our own introspection
    return [row[2] for row in cur.fetchall()]  # row[2] = referenced table name


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class SchemaIndex:
    """An in-memory embedding index over a database's table schemas."""

    def __init__(self, tables: Dict[str, str], vectors: Dict[str, List[float]],
                 fk_map: Dict[str, List[str]]):
        self.tables = tables      # name -> DDL text
        self.vectors = vectors    # name -> embedding of that DDL
        self.fk_map = fk_map      # name -> [referenced table names]

    @classmethod
    def build(cls, conn, embed: EmbedFn) -> "SchemaIndex":
        tables = table_schemas(conn)
        names = list(tables)
        fk_map = {n: _fk_referenced_tables(conn, n) for n in names}
        vectors = {}
        if names:
            embedded = embed([tables[n] for n in names])
            vectors = {n: v for n, v in zip(names, embedded)}
        return cls(tables, vectors, fk_map)

    def retrieve(self, question: str, embed: EmbedFn, top_k: int = 3) -> str:
        """Return the DDL of the tables most relevant to `question`, FK-expanded."""
        names = list(self.tables)
        if len(names) <= top_k:
            chosen = set(names)  # small schema: retrieval adds nothing, use all
        else:
            qvec = embed([question])[0]
            ranked = sorted(names, key=lambda n: _cosine(qvec, self.vectors[n]), reverse=True)
            chosen = set(ranked[:top_k])

        # Expand along foreign keys so the model can still write the necessary JOINs.
        expanded = set(chosen)
        for n in chosen:
            for ref in self.fk_map.get(n, []):
                if ref in self.tables:
                    expanded.add(ref)

        return "\n\n".join(self.tables[n] for n in sorted(expanded))
