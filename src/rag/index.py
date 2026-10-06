"""Embed the store summaries with OpenAI and search them by meaning."""
import json
import os
from pathlib import Path
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv
from src.ingest.load_raw import get_connection

load_dotenv()
INDEX_DIR = Path(__file__).resolve().parents[2] / "data" / "rag"
EMBED_MODEL = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")


def embed(texts):
    resp = OpenAI().embeddings.create(model=EMBED_MODEL, input=texts)
    vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)   # normalize for cosine similarity


def build_index():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT STORE_NBR, WEEK_START, SUMMARY FROM SALES_DB.ANALYTICS.STORE_SUMMARIES ORDER BY STORE_NBR")
        rows = cur.fetchall()
    finally:
        conn.close()

    docs = [{"store_nbr": int(r[0]), "week_start": str(r[1]), "text": r[2]} for r in rows]
    vecs = embed([d["text"] for d in docs])
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(INDEX_DIR / "vectors.npy", vecs)
    (INDEX_DIR / "docs.json").write_text(json.dumps(docs))
    print(f"Indexed {len(docs)} documents with {EMBED_MODEL}")


def search(question, k=3):
    vecs = np.load(INDEX_DIR / "vectors.npy")
    docs = json.loads((INDEX_DIR / "docs.json").read_text())
    scores = vecs @ embed([question])[0]
    top = np.argsort(scores)[::-1][:k]
    return [{**docs[i], "score": round(float(scores[i]), 3)} for i in top]


def refresh():
    """Pipeline entry point: rebuild summaries, then re-embed them."""
    from src.rag.summaries import build_summaries
    build_summaries()
    build_index()
