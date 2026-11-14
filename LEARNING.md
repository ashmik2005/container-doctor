# LEARNING.md: Container Doctor interview prep

One section per build stage: what was built, the key concepts in plain words,
design decisions (decision → why → alternative), and interview questions with
model answers. Read the questions first and try answering before reading mine.

---

## Stage 0: Setup

### What we built
- Verified Docker 29.3, Docker Compose v5, Python 3.12, git.
- Installed Ollama (local model server) and pulled two models:
  `qwen2.5:3b` (chat + tool calling) and `nomic-embed-text` (embeddings).
- Created a virtual environment `.venv/` with a single dependency: `ollama`.
- `smoke_test.py`: one chat call and one embedding call, to prove the stack works.
- `git init`, `.gitignore`, `requirements.txt`.

### Key concepts

**Ollama is just an HTTP server.** It runs as a systemd service on
`localhost:11434`, loads model weights into RAM and exposes endpoints like
`POST /api/chat` and `POST /api/embed`. The `ollama` Python library is a thin
wrapper that builds those JSON requests. You could do the same with `curl`:
```bash
curl localhost:11434/api/chat -d '{"model":"qwen2.5:3b","stream":false,
  "messages":[{"role":"user","content":"hi"}]}'
```
This matters later: "tool calling" is not magic inside Python, it is just extra
JSON fields in that request and response.

**Quantization.** A 3B-parameter model in 16-bit floats needs about 6 GB.
Ollama's default `qwen2.5:3b` is quantized to about 4 bits per weight (Q4_K_M),
so it is about 1.9 GB on disk and fits in RAM with room to spare. The cost is a
small quality loss. On a CPU-only laptop, smaller = faster tokens per second.

**Embedding.** A function that maps text to a fixed-length list of numbers
(here 768) such that texts with similar meaning end up close together.
"container killed, out of memory" and "exit code 137 OOM" get nearby vectors
even though they share almost no words. That is what makes semantic search
possible in Stage 3.

**Virtual environment.** `python3 -m venv .venv` makes a private copy of the
Python interpreter with its own `site-packages`. Project dependencies don't
pollute the system Python, and Ubuntu 24.04 blocks system-wide `pip install`
anyway (PEP 668, "externally managed environment").

**Docker group.** The Docker daemon runs as root and listens on
`/var/run/docker.sock`, owned by `root:docker`. Users in the `docker` group can
use the socket without `sudo`. Our agent shells out to `docker ...`, so it needs
this, because it can't type a sudo password.

### Design decisions
| Decision | Why | Alternative considered |
|---|---|---|
| Ollama for local models | Free, one-command install, OpenAI-style chat API with native tool-call support, official Python client | llama.cpp server (more knobs, more setup); HuggingFace transformers (heavy, slow on CPU) |
| `qwen2.5:3b` as the chat model | Trained for tool calling; 3B fits CPU + 15 GB RAM at usable speed | `llama3.2:3b` (also tool-capable, weaker at JSON args in practice); 7B models (better reasoning, ~2–3× slower on CPU) |
| `nomic-embed-text` for embeddings | Small (~270 MB), 768-dim, good retrieval quality, runs in Ollama so there's no second runtime | `sentence-transformers` in Python (adds PyTorch, ~2 GB of deps) |
| Docker group, not rootless Docker | Already configured, zero friction on a personal laptop | Rootless Docker: safer (docker group = root-equivalent) but different networking/ports behaviour |
| Install deps only when a stage needs them | Every dependency has a reason I can explain | Install everything up front |

### Interview questions
1. **"Why run the model locally instead of calling the OpenAI API?"**
   Free, no API key, no data leaves the machine (container logs can contain
   secrets), and it forced me to deal with a small model's limits, which made
   me build proper validation. The downside is a much weaker model and slow
   CPU inference.
2. **"Your user is in the docker group. Any security concern?"**
   Yes. Docker group membership is effectively root: you can run
   `docker run -v /:/host ...` and edit any file. Acceptable on a single-user
   laptop. In production I'd use rootless Docker, or give the agent a
   restricted proxy to the Docker API that only allows read endpoints.
3. **"What is quantization and what does it cost you?"**
   Storing weights in fewer bits (4 instead of 16). It's about 4× less memory
   and faster on CPU because inference is memory-bandwidth bound. The cost is
   a small accuracy drop, and it hurts small models relatively more.
