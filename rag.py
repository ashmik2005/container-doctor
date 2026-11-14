"""Runbook RAG: chunk the markdown runbooks, embed them, store them in ChromaDB, search them.

Build the index:  .venv/bin/python rag.py
Try a search:     .venv/bin/python rag.py "exit code 137"
"""
import sys
from pathlib import Path

import chromadb
import ollama

EMBED_MODEL = "nomic-embed-text"
RUNBOOK_DIR = Path(__file__).parent / "runbooks"
DB_DIR = Path(__file__).parent / "chroma_db"
COLLECTION = "runbooks"

# nomic-embed-text was trained with task prefixes: documents and queries get
# different prefixes so the model knows which side of the search each text is on.
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


def chunk_runbook(path: Path) -> list[dict]:
    """Split one runbook into one chunk per '## ' section.

    Each chunk starts with the runbook's '# Title' so a section like "## Fix"
    still says WHAT it is fixing. Without the title, all "Fix" sections would
    look alike to the embedding model.
    """
    lines = path.read_text().splitlines()
    title = lines[0].lstrip("# ").strip()
    chunks, current = [], []
    for line in lines[1:]:
        if line.startswith("## ") and current:
            chunks.append(current)
            current = []
        current.append(line)
    if current:
        chunks.append(current)

    return [
        {
            "id": f"{path.stem}#{i}",
            "text": f"{title}\n" + "\n".join(c).strip(),
            "source": path.name,
        }
        for i, c in enumerate(chunks)
        if "\n".join(c).strip()
    ]


def embed(texts: list[str]) -> list[list[float]]:
    """One HTTP call to Ollama, one 768-number vector per text."""
    return ollama.embed(model=EMBED_MODEL, input=texts).embeddings


def get_collection(client=None):
    client = client or chromadb.PersistentClient(path=str(DB_DIR))
    # cosine distance = 1 - cosine similarity. embedding_function=None because
    # we always pass our own vectors (Chroma would otherwise download its own model).
    return client.get_or_create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None)


def build_index() -> int:
    """(Re)build the vector store from scratch. Returns the number of chunks."""
    client = chromadb.PersistentClient(path=str(DB_DIR))
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)  # rebuild cleanly: no stale chunks
    collection = get_collection(client)

    chunks = [c for path in sorted(RUNBOOK_DIR.glob("*.md")) for c in chunk_runbook(path)]
    collection.add(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        metadatas=[{"source": c["source"]} for c in chunks],
        embeddings=embed([DOC_PREFIX + c["text"] for c in chunks]),
    )
    return len(chunks)


def search(query: str, k: int = 2) -> list[dict]:
    """Return the k most relevant RUNBOOKS (not chunks) for the query.

    We search over small chunks (precise matching) but return whole runbooks
    (full context: symptoms + how to confirm + fix). Several chunks of the same
    runbook may match; we keep each runbook once, at its best score.
    """
    collection = get_collection()
    result = collection.query(
        query_embeddings=embed([QUERY_PREFIX + query]),
        n_results=k * 4,  # over-fetch, because several hits may be the same runbook
    )
    seen, hits = set(), []
    for meta, distance in zip(result["metadatas"][0], result["distances"][0]):
        source = meta["source"]
        if source in seen:
            continue
        seen.add(source)
        hits.append({
            "source": source,
            "similarity": round(1 - distance, 3),
            "text": (RUNBOOK_DIR / source).read_text(),
        })
        if len(hits) == k:
            break
    return hits


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for hit in search(" ".join(sys.argv[1:]), k=3):
            print(f"{hit['similarity']:.3f}  {hit['source']}")
    else:
        print(f"Indexed {build_index()} chunks from {RUNBOOK_DIR}/ into {DB_DIR}/")
