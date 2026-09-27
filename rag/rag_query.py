#!/usr/bin/env python3
# ============================================================================
#  rag_query.py - retrieval function the pipeline calls over the doc vector store.
#
#  query(question, k=4) embeds the question, retrieves the k most relevant chunks
#  from the ChromaDB store built by build_vectorstore.py, APPENDS the retrieval to
#  rag/retrieval_log.jsonl (for auditing the RAG over the documentation process),
#  and returns the hits. If the store / chromadb is unavailable it logs the miss
#  and returns [] so the pipeline never breaks.
#
#  CLI:  python rag_query.py "how do iCE40 flip-flops power up?"  [k]
# ============================================================================
import os, sys, json, time

HERE  = os.path.dirname(os.path.abspath(__file__))
DBDIR = os.path.join(HERE, "chroma_db")
COLL  = "csaw_docs"
LOG   = os.path.join(HERE, "retrieval_log.jsonl")

_client = None
_coll = None

def _get_collection():
    global _client, _coll
    if _coll is not None: return _coll
    import chromadb
    from chromadb.utils import embedding_functions as ef
    _client = chromadb.PersistentClient(path=DBDIR)
    # match whichever embedder built the store; try sentence-transformers, else ONNX
    try:
        import sentence_transformers  # noqa: F401
        emb = ef.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    except Exception:
        emb = ef.ONNXMiniLM_L6_V2()
    _coll = _client.get_collection(COLL, embedding_function=emb)
    return _coll

def _log(entry):
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

def query(question, k=4, stage=None, decision=None):
    """Return [{source, chunk, distance, text}], logging the retrieval AND the
    decision it drove (how the retrieved evidence + distances shaped the design)."""
    try:
        coll = _get_collection()
        res = coll.query(query_texts=[question], n_results=k)
        hits = []
        for i in range(len(res["ids"][0])):
            meta = res["metadatas"][0][i] or {}
            hits.append({
                "source": meta.get("source"), "chunk": meta.get("chunk"),
                "distance": round(float(res["distances"][0][i]), 4) if res.get("distances") else None,
                "text": res["documents"][0][i],
            })
        _log({"stage": stage, "query": question, "k": k,
              "retrieved": [{"source": h["source"], "chunk": h["chunk"], "distance": h["distance"]} for h in hits],
              "decision": decision})
        return hits
    except Exception as e:
        _log({"stage": stage, "query": question, "k": k, "error": str(e),
              "retrieved": [], "decision": decision})
        return []

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: rag_query.py \"question\" [k]"); sys.exit(1)
    q = sys.argv[1]; k = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    for h in query(q, k, stage="cli"):
        print("[%s#%s d=%s] %s...\n" % (h["source"], h["chunk"], h["distance"], h["text"][:200]))
