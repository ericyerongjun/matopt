"""The recall index: hybrid retrieval over memory files and conversations in SQLite.

One SQLite file (``<data_dir>/recall.sqlite3``) holds every searchable chunk,
grouped into *sources* identified by ``(scope, owner)``: the memory folder is
``("memory", "global")`` and each conversation is ``("chat", <id>)``. A source
is replaced whole when its signature changes, so the index is a cache that can
always be rebuilt from the files and the conversation table.

Retrieval fuses up to three rankings with reciprocal rank fusion (RRF,
Cormack et al. 2009; score ``Σ 1/(60 + rank)``), which needs no score
calibration between rankers:

1. **Lexical** — SQLite FTS5 BM25 over a LaTeX-aware form of the text
   (:func:`terms.index_text`). Exact notation, identifiers and named results
   are where dense retrievers are weakest and BM25 is strongest.
2. **Dense** (optional) — cosine similarity over local embeddings
   (:mod:`.embeddings`), brute force with numpy, computed lazily for chunks
   that lack a vector for the configured model.
3. **Graph** — a relation-free term graph after LinearRAG (arXiv 2510.10114)
   and HippoRAG 2 (arXiv 2502.14802): chunks and the terms they mention form
   a bipartite graph, and Personalized PageRank is seeded from the query's
   terms and the first-stage hits. It surfaces chunks that share a named
   result or identifier with the hits without sharing their words — the
   multi-hop case ("the dataset we used for the Lasso fit"). Building it costs
   no model calls; hub terms mentioned by a large share of chunks are dropped.

Indexing never calls a model either: the only costs are FTS inserts and, with
embeddings on, one local encoder pass per new chunk.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

from app.agent.memory import terms as termlib

logger = logging.getLogger(__name__)

RRF_K = 60
POOL = 50
DAMPING = 0.85
PPR_ITERATIONS = 30
HUB_SHARE = 0.3
MAX_GRAPH_EDGES = 4_000
MAX_LAZY_EMBEDDINGS = 4_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    scope TEXT NOT NULL,
    owner TEXT NOT NULL,
    signature TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, owner)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    owner TEXT NOT NULL,
    ref TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT 0,
    UNIQUE (scope, owner, ref)
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(scope, owner);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(body, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS mentions (
    term TEXT NOT NULL,
    chunk_id INTEGER NOT NULL,
    PRIMARY KEY (term, chunk_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_mentions_chunk ON mentions(chunk_id);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    vec BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS session_notes (
    conversation_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    injected_version INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
"""


@dataclass
class Doc:
    ref: str
    text: str
    title: str = ""
    meta: dict = field(default_factory=dict)
    created_at: float = 0.0


@dataclass
class Hit:
    scope: str
    owner: str
    ref: str
    title: str
    text: str
    meta: dict
    created_at: float
    score: float
    ranks: dict[str, int]


def rrf(rankings: Iterable[list[int]], k: int = RRF_K) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def personalized_pagerank(edges: list[tuple[str, int]], reset: dict, *, damping: float = DAMPING,
                          iterations: int = PPR_ITERATIONS) -> dict:
    """PPR over an undirected bipartite graph of ``(term, chunk_id)`` edges.

    ``reset`` maps nodes (``("t", term)`` or ``("c", chunk_id)``) to restart
    weights; it is normalised here. Returns the stationary scores.
    """
    neighbours: dict[tuple, list[tuple]] = {}
    for term, chunk in edges:
        a, b = ("t", term), ("c", chunk)
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    total = sum(w for node, w in reset.items() if node in neighbours and w > 0)
    if not total:
        return {}
    restart = {node: w / total for node, w in reset.items() if node in neighbours and w > 0}
    score = dict(restart)
    for _ in range(iterations):
        nxt = {node: (1 - damping) * w for node, w in restart.items()}
        for node, value in score.items():
            out = neighbours[node]
            share = damping * value / len(out)
            for other in out:
                nxt[other] = nxt.get(other, 0.0) + share
        score = nxt
    return score


class RecallIndex:
    def __init__(self, path: Path, *, embedder: Callable[[], object] | None = None, graph: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._embedder = embedder or (lambda: None)
        self.graph = graph
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── sources ─────────────────────────────────────────────────────────

    def signature(self, scope: str, owner: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT signature FROM sources WHERE scope=? AND owner=?",
                                     (scope, owner)).fetchone()
        return row["signature"] if row else None

    def signatures(self, scope: str) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT owner, signature FROM sources WHERE scope=?", (scope,)).fetchall()
        return {r["owner"]: r["signature"] for r in rows}

    def replace_source(self, scope: str, owner: str, signature: str, docs: list[Doc]) -> bool:
        """Replace every chunk of ``(scope, owner)``; a no-op when the signature is unchanged."""
        if self.signature(scope, owner) == signature:
            return False
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._delete_chunks(scope, owner)
                seen: set[str] = set()
                for doc in docs:
                    if not doc.text.strip() or doc.ref in seen:
                        continue
                    seen.add(doc.ref)
                    cur = self._conn.execute(
                        "INSERT INTO chunks (scope, owner, ref, title, text, meta, created_at) VALUES (?,?,?,?,?,?,?)",
                        (scope, owner, doc.ref, doc.title, doc.text, json.dumps(doc.meta, ensure_ascii=False),
                         float(doc.created_at or 0)))
                    chunk_id = cur.lastrowid
                    found = termlib.terms(f"{doc.title}\n{doc.text}")
                    body = "\n".join([doc.title, termlib.index_text(doc.text), " ".join(found)])
                    self._conn.execute("INSERT INTO chunks_fts (rowid, body) VALUES (?, ?)", (chunk_id, body))
                    self._conn.executemany("INSERT OR IGNORE INTO mentions (term, chunk_id) VALUES (?, ?)",
                                           [(t, chunk_id) for t in found])
                self._conn.execute(
                    "INSERT INTO sources (scope, owner, signature, updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(scope, owner) DO UPDATE SET signature=excluded.signature, "
                    "updated_at=excluded.updated_at", (scope, owner, signature, time.time()))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return True

    def delete_source(self, scope: str, owner: str | None = None) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                owners = [owner] if owner is not None else [
                    r["owner"] for r in self._conn.execute("SELECT DISTINCT owner FROM chunks WHERE scope=?",
                                                           (scope,))]
                for o in owners:
                    self._delete_chunks(scope, o)
                if owner is None:
                    self._conn.execute("DELETE FROM sources WHERE scope=?", (scope,))
                else:
                    self._conn.execute("DELETE FROM sources WHERE scope=? AND owner=?", (scope, owner))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _delete_chunks(self, scope: str, owner: str) -> None:
        ids = [r["id"] for r in self._conn.execute("SELECT id FROM chunks WHERE scope=? AND owner=?",
                                                    (scope, owner))]
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            marks = ",".join("?" * len(batch))
            self._conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", batch)
            self._conn.execute(f"DELETE FROM mentions WHERE chunk_id IN ({marks})", batch)
            self._conn.execute(f"DELETE FROM vectors WHERE chunk_id IN ({marks})", batch)
        self._conn.execute("DELETE FROM chunks WHERE scope=? AND owner=?", (scope, owner))

    def get(self, scope: str, owner: str, ref: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM chunks WHERE scope=? AND owner=? AND ref=?",
                                     (scope, owner, ref)).fetchone()
        return self._row(row) if row else None

    # ── search ──────────────────────────────────────────────────────────

    @staticmethod
    def _filter(scope: str, owners: Optional[Iterable[str]], exclude: Iterable[str]) -> tuple[str, list]:
        sql, args = "c.scope = ?", [scope]
        if owners is not None:
            owners = list(owners)
            if not owners:
                return "0", []
            sql += f" AND c.owner IN ({','.join('?' * len(owners))})"
            args += owners
        exclude = list(exclude)
        if exclude:
            sql += f" AND c.owner NOT IN ({','.join('?' * len(exclude))})"
            args += exclude
        return sql, args

    def search(self, query: str, *, scope: str, owners: Optional[Iterable[str]] = None,
               exclude: Iterable[str] = (), limit: int = 8, recency_days: float | None = None,
               now: float | None = None) -> list[Hit]:
        where, args = self._filter(scope, owners, exclude)
        if where == "0":
            return []
        lexical = self._lexical(query, where, args)
        dense = self._dense(query, where, args)
        first = rrf([lexical, dense])
        rankings = [lexical, dense]
        graph: list[int] = []
        if self.graph:
            graph = self._graph(query, first, where, args)
            rankings.append(graph)
        fused = rrf(rankings)
        if not fused:
            return []
        rows = self._rows(list(fused))
        now = time.time() if now is None else now
        if recency_days:
            for cid, row in rows.items():
                age = max(0.0, now - float(row["created_at"] or now)) / 86_400
                fused[cid] *= 1.0 + 0.5 * math.exp(-age / recency_days)
        order = sorted((cid for cid in fused if cid in rows), key=lambda c: fused[c], reverse=True)[:limit]
        positions = [{c: i for i, c in enumerate(r, 1)} for r in (lexical, dense, graph)]
        hits = []
        for cid in order:
            row = rows[cid]
            ranks = {name: pos[cid] for name, pos in zip(("lexical", "dense", "graph"), positions) if cid in pos}
            hits.append(Hit(scope=row["scope"], owner=row["owner"], ref=row["ref"], title=row["title"],
                            text=row["text"], meta=row["meta"], created_at=row["created_at"],
                            score=round(fused[cid], 6), ranks=ranks))
        return hits

    def _lexical(self, query: str, where: str, args: list) -> list[int]:
        match = termlib.fts_query(query)
        if not match:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                    f"WHERE chunks_fts MATCH ? AND {where} ORDER BY bm25(chunks_fts) LIMIT ?",
                    (match, *args, POOL)).fetchall()
        except sqlite3.OperationalError:
            logger.debug("recall: FTS query failed for %r", match, exc_info=True)
            return []
        return [r["id"] for r in rows]

    def _dense(self, query: str, where: str, args: list) -> list[int]:
        embedder = self._embedder()
        if embedder is None or not query.strip():
            return []
        try:
            self._embed_missing(embedder, where, args)
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT v.chunk_id, v.vec FROM vectors v JOIN chunks c ON c.id = v.chunk_id "
                    f"WHERE v.model = ? AND {where}", (embedder.name, *args)).fetchall()
            if not rows:
                return []
            matrix = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
            q = embedder.embed([query])[0]
            sims = matrix @ q
            top = np.argsort(-sims)[:POOL]
            return [rows[i]["chunk_id"] for i in top if sims[i] > 0]
        except Exception:  # noqa: BLE001 — dense retrieval is an optional ranker
            logger.warning("recall: dense retrieval failed", exc_info=True)
            return []

    def _embed_missing(self, embedder, where: str, args: list) -> None:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT c.id, c.title, c.text FROM chunks c LEFT JOIN vectors v ON v.chunk_id = c.id "
                f"WHERE (v.chunk_id IS NULL OR v.model != ?) AND {where} LIMIT ?",
                (embedder.name, *args, MAX_LAZY_EMBEDDINGS)).fetchall()
        for start in range(0, len(rows), 64):
            batch = rows[start:start + 64]
            vectors = embedder.embed([f"{r['title']}\n{r['text']}" for r in batch])
            with self._lock:
                self._conn.executemany(
                    "INSERT INTO vectors (chunk_id, model, vec) VALUES (?,?,?) ON CONFLICT(chunk_id) "
                    "DO UPDATE SET model=excluded.model, vec=excluded.vec",
                    [(r["id"], embedder.name, np.asarray(v, dtype=np.float32).tobytes())
                     for r, v in zip(batch, vectors)])

    def _graph(self, query: str, first: dict[int, float], where: str, args: list) -> list[int]:
        seeds = set(termlib.terms(query)) | set(termlib.query_terms(query))
        pool = sorted(first, key=first.get, reverse=True)[:POOL]
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM chunks c WHERE {where}", args).fetchone()[0]
            if not total:
                return []
            candidates = set(seeds)
            if pool:
                marks = ",".join("?" * len(pool))
                candidates |= {r["term"] for r in self._conn.execute(
                    f"SELECT term FROM mentions WHERE chunk_id IN ({marks})", pool)}
            if not candidates:
                return []
            terms_list = sorted(candidates)
            df: dict[str, int] = {}
            for start in range(0, len(terms_list), 400):
                batch = terms_list[start:start + 400]
                marks = ",".join("?" * len(batch))
                for r in self._conn.execute(
                        f"SELECT m.term, COUNT(*) AS n FROM mentions m JOIN chunks c ON c.id = m.chunk_id "
                        f"WHERE m.term IN ({marks}) AND {where} GROUP BY m.term", (*batch, *args)):
                    df[r["term"]] = r["n"]
            hub = max(3, int(total * HUB_SHARE))
            usable = [t for t, n in df.items() if n <= hub and (n > 1 or t in seeds)]
            if not usable:
                return []
            edges: list[tuple[str, int]] = []
            for start in range(0, len(usable), 400):
                batch = usable[start:start + 400]
                marks = ",".join("?" * len(batch))
                edges += [(r["term"], r["chunk_id"]) for r in self._conn.execute(
                    f"SELECT m.term, m.chunk_id FROM mentions m JOIN chunks c ON c.id = m.chunk_id "
                    f"WHERE m.term IN ({marks}) AND {where} LIMIT ?", (*batch, *args, MAX_GRAPH_EDGES))]
                if len(edges) >= MAX_GRAPH_EDGES:
                    break
        if not edges:
            return []
        reset: dict = {}
        seed_terms = [t for t in usable if t in seeds]
        if seed_terms:
            idf = {t: math.log(1 + total / df[t]) for t in seed_terms}
            norm = sum(idf.values())
            for t in seed_terms:
                reset[("t", t)] = 0.7 * idf[t] / norm
        if pool:
            norm = sum(first[c] for c in pool)
            share = 0.3 if seed_terms else 1.0
            for c in pool:
                reset[("c", c)] = reset.get(("c", c), 0.0) + share * first[c] / norm
        scores = personalized_pagerank(edges, reset)
        ranked = sorted(((node[1], s) for node, s in scores.items() if node[0] == "c" and s > 0),
                        key=lambda x: x[1], reverse=True)
        return [c for c, _ in ranked[:POOL]]

    def _rows(self, ids: list[int]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        with self._lock:
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                marks = ",".join("?" * len(batch))
                for row in self._conn.execute(f"SELECT * FROM chunks WHERE id IN ({marks})", batch):
                    out[row["id"]] = self._row(row)
        return out

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data["meta"] = json.loads(data.get("meta") or "{}")
        except json.JSONDecodeError:
            data["meta"] = {}
        return data

    # ── session notes (per-conversation working memory) ─────────────────

    def get_notes(self, cid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM session_notes WHERE conversation_id=?", (cid,)).fetchone()
        return dict(row) if row else None

    def put_notes(self, cid: str, text: str) -> dict:
        now = time.time()
        with self._lock:
            if text.strip():
                self._conn.execute(
                    "INSERT INTO session_notes (conversation_id, text, version, injected_version, updated_at) "
                    "VALUES (?,?,1,0,?) ON CONFLICT(conversation_id) DO UPDATE SET text=excluded.text, "
                    "version=session_notes.version+1, updated_at=excluded.updated_at", (cid, text, now))
            else:
                self._conn.execute("DELETE FROM session_notes WHERE conversation_id=?", (cid,))
        return self.get_notes(cid) or {"conversation_id": cid, "text": "", "version": 0, "injected_version": 0,
                                       "updated_at": now}

    def mark_notes_injected(self, cid: str, version: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE session_notes SET injected_version=? WHERE conversation_id=?", (version, cid))

    def delete_notes(self, cid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM session_notes WHERE conversation_id=?", (cid,))
