"""Compare chunking strategies for the runbook search, with numbers.

For each strategy we embed the runbooks into a throwaway in-memory Chroma
collection, run 20 realistic queries whose correct runbook we know, and count
how often that runbook comes back first (hit@1) or within the top 2 (hit@2).

Run:  .venv/bin/python eval_retrieval.py
"""
import chromadb

from rag import DOC_PREFIX, QUERY_PREFIX, RUNBOOK_DIR, chunk_runbook, embed

# (query, expected runbook). Queries are error text or symptoms a user/agent
# would actually see, written BEFORE looking at the results.
QUERIES = [
    ("container exited with code 137", "oom_killed.md"),
    ("OOMKilled true", "oom_killed.md"),
    ("KeyError: 'DATABASE_URL'", "missing_env_var.md"),
    ("Bind for 0.0.0.0:8080 failed: port is already allocated", "port_conflict.md"),
    ("manifest unknown when pulling image", "bad_image_tag.md"),
    ("psql: could not connect to server: Connection refused db:5432 at startup", "db_not_ready.md"),
    ("FileNotFoundError: [Errno 2] No such file or directory: '/config/app.yaml'", "wrong_volume_path.md"),
    ("PermissionError: [Errno 13] Permission denied: '/var/lib/app/data.txt'", "permission_denied.md"),
    ("socket.gaierror: [Errno -2] Name or service not known", "dns_service_name.md"),
    ("container status Restarting (1) again and again", "crash_loop.md"),
    ("Up 5 minutes (unhealthy)", "healthcheck_failing.md"),
    ('exec: "pyhton": executable file not found in $PATH', "command_not_found.md"),
    ("OSError: [Errno 28] No space left on device", "disk_full.md"),
    ('nginx: [emerg] unexpected "}" in /etc/nginx/nginx.conf:9', "config_syntax_error.md"),
    ("Serving HTTP on 127.0.0.1 port 8000 but curl from host gets connection reset", "app_binds_localhost.md"),
    ('FATAL: password authentication failed for user "app"', "wrong_credentials.md"),
    ("ModuleNotFoundError: No module named 'requests'", "missing_dependency.md"),
    ("OSError: [Errno 30] Read-only file system", "read_only_filesystem.md"),
    ("container exits immediately with code 0 and no error", "exits_immediately.md"),
    ("app listens on port 80 but docker maps 8080:8080", "wrong_port_mapping.md"),
]


def whole_file_chunks(path):
    return [{"id": path.stem, "text": path.read_text(), "source": path.name}]


def section_chunks_no_title(path):
    chunks = chunk_runbook(path)
    for c in chunks:
        c["text"] = c["text"].split("\n", 1)[1] if "\n" in c["text"] else c["text"]
    return chunks


STRATEGIES = {
    "whole file (1 chunk per runbook)": whole_file_chunks,
    "## sections, no title": section_chunks_no_title,
    "## sections + title (used in rag.py)": chunk_runbook,
}


def evaluate(chunker) -> tuple[int, int, int]:
    client = chromadb.EphemeralClient()
    collection = client.create_collection(
        "tmp", metadata={"hnsw:space": "cosine"}, embedding_function=None)
    chunks = [c for p in sorted(RUNBOOK_DIR.glob("*.md")) for c in chunker(p)]
    collection.add(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        metadatas=[{"source": c["source"]} for c in chunks],
        embeddings=embed([DOC_PREFIX + c["text"] for c in chunks]),
    )
    query_vectors = embed([QUERY_PREFIX + q for q, _ in QUERIES])
    hit1 = hit2 = 0
    for (query, expected), vector in zip(QUERIES, query_vectors):
        result = collection.query(query_embeddings=[vector], n_results=min(10, len(chunks)))
        ranked = list(dict.fromkeys(m["source"] for m in result["metadatas"][0]))  # dedupe, keep order
        hit1 += ranked[:1] == [expected]
        hit2 += expected in ranked[:2]
    client.delete_collection("tmp")
    return len(chunks), hit1, hit2


if __name__ == "__main__":
    n = len(QUERIES)
    print(f"| Chunking strategy | Chunks | hit@1 | hit@2 |\n|---|---|---|---|")
    for name, chunker in STRATEGIES.items():
        count, hit1, hit2 = evaluate(chunker)
        print(f"| {name} | {count} | {hit1}/{n} | {hit2}/{n} |")
