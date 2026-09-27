#!/usr/bin/env python3
# ============================================================================
#  build_vectorstore.py - build a ChromaDB vector store over the documentation.
#
#  Corpus (rag/corpus/):
#    * FPGA-AN-02109  iCE40 Design Guidelines            (Lattice PDF -> text)
#    * FPGA-TN-02011  Advanced iCE40 I2C & SPI Hardened IP User Guide (Lattice PDF)
#    * the CSAW'26 challenge docs (IP-core doc, RP2040 micropython, qualifier README)
#    * Serial Peripheral Interface (simple.wikipedia.org)
#
#  Embeddings: prefers sentence-transformers "all-MiniLM-L6-v2"; if that (torch)
#  backend is unavailable, falls back to ChromaDB's built-in ONNX MiniLM - the
#  SAME model, no torch needed - so the store always builds.
#
#  Output: a persistent Chroma collection under rag/chroma_db/.
# ============================================================================
import os, glob, sys

HERE   = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus")
DBDIR  = os.path.join(HERE, "chroma_db")
COLL   = "csaw_docs"
CHUNK, OVERLAP = 900, 150

def chunk_text(txt):
    txt = " ".join(txt.split())
    out, i = [], 0
    while i < len(txt):
        out.append(txt[i:i+CHUNK]); i += CHUNK - OVERLAP
    return [c for c in out if c.strip()]

def get_embedder():
    """sentence-transformers if importable, else ChromaDB's built-in ONNX MiniLM."""
    try:
        from chromadb.utils import embedding_functions as ef
        try:
            import sentence_transformers  # noqa: F401
            print("  embeddings: sentence-transformers all-MiniLM-L6-v2")
            return ef.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        except Exception:
            print("  embeddings: ChromaDB built-in ONNX all-MiniLM-L6-v2 (sentence-transformers/torch unavailable)")
            return ef.ONNXMiniLM_L6_V2()
    except Exception as e:
        print("  (embedding function unavailable: %s)" % e); return None

def main():
    try:
        import chromadb
    except ImportError:
        print("chromadb not installed; skipping vector-store build (RAG will be a no-op).")
        return 0
    files = sorted(glob.glob(os.path.join(CORPUS, "*")))
    if not files:
        print("no corpus files under rag/corpus/"); return 1
    client = chromadb.PersistentClient(path=DBDIR)
    try: client.delete_collection(COLL)
    except Exception: pass
    emb = get_embedder()
    coll = client.create_collection(COLL, embedding_function=emb) if emb else client.create_collection(COLL)
    docs, ids, metas = [], [], []
    for f in files:
        try: txt = open(f, encoding="utf-8", errors="ignore").read()
        except Exception: continue
        src = os.path.basename(f)
        for j, ch in enumerate(chunk_text(txt)):
            docs.append(ch); ids.append("%s#%d" % (src, j)); metas.append({"source": src, "chunk": j})
    # add in batches
    B = 128
    for i in range(0, len(docs), B):
        coll.add(documents=docs[i:i+B], ids=ids[i:i+B], metadatas=metas[i:i+B])
    print("build_vectorstore.py: indexed %d chunks from %d documents into %s" %
          (len(docs), len(files), DBDIR))
    return 0

if __name__ == "__main__":
    sys.exit(main())
