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

---

## Stage 1: Minimal agent (2 tools)

### What we built
- `tools.py`: `list_containers()` and `get_logs(container, tail)`, both thin
  wrappers around `docker` CLI commands, plus `TOOLS` (name → function) and
  `TOOL_SCHEMAS` (JSON descriptions the model reads).
- `agent.py`: the agent loop, which prints every message so you can see the flow.
- `scenarios/01_missing_env_var/`: a container that crashes because
  `DATABASE_URL` is not set.

### How tool calling works under the hood
The model cannot run anything. It only produces text. "Tool calling" is a
convention between us and the model:

1. **We describe tools** in the request (`tools=TOOL_SCHEMAS`). Ollama inserts
   these JSON schemas into the prompt using the model's chat template. Qwen2.5
   was fine-tuned on examples where, given such schemas, it answers with a
   special block like `<tool_call>{"name": "get_logs", "arguments": {...}}</tool_call>`.
2. **Ollama parses** that block out of the generated text and returns it as
   structured data instead of plain content:
   ```json
   {"role": "assistant", "content": "",
    "tool_calls": [{"function": {"name": "get_logs",
                                 "arguments": {"container": "web"}}}]}
   ```
3. **Our code executes it**: look up `TOOLS["get_logs"]`, call it with the
   arguments, get a string.
4. **We append the result** as a message with `role: "tool"` and call the model
   again with the whole history. The model now "sees" the logs and continues.
5. **Stop condition**: when a reply contains no `tool_calls`, it is the final answer.

That loop (model → tool calls → results → model …) **is** the agent. Frameworks
like LangChain wrap exactly this.

### Real transcript (scenario 01, 17 s on CPU)
```
SYSTEM    You are Container Doctor ... (instructions + answer format)
USER      My web app is down. What is wrong?
ASSISTANT [tool call] list_containers({})
TOOL      web  python:3.12-alpine  Exited (1) 2 minutes ago
ASSISTANT The web container is the one that's not working ...
          [tool call] get_logs({"container": "web"})
TOOL      Traceback ... KeyError: 'DATABASE_URL'
ASSISTANT ROOT CAUSE: ... unable to locate the 'DATABASE_URL' environment variable ...
          FIX: add it under environment: in docker compose ...
```

### Key concepts
- **The message list is the agent's only memory.** The model is stateless. Every
  call sends the full history again, which is why long tool outputs are a
  problem (context window) and why we truncate them in Stage 2.
- **Roles:** `system` (standing instructions), `user` (the question),
  `assistant` (model output, possibly with `tool_calls`), `tool` (results we feed back).
- **`temperature: 0`:** always pick the most likely next token. Makes runs
  repeatable, which we need for evaluation.
- **`subprocess.run([...])` with a list:** no shell is started, so a malicious
  argument like `web; rm -rf /` is just a strange container name, not a second command.

### Design decisions
| Decision | Why | Alternative considered |
|---|---|---|
| Hand-written loop, no framework | Every step is visible and explainable; it's about 40 lines | LangChain/LangGraph agents (hide the loop) |
| Explicit JSON schemas for tools | I control exactly what the model reads; descriptions double as prompting | The ollama lib can build schemas from Python docstrings (less control) |
| Tools return strings | The model only reads text; errors are also returned as text so the model can react | Return dicts and JSON-encode them |
| Shell out to the `docker` CLI | Output looks exactly like what a human sees and what's in runbooks; no extra dependency | `docker` Python SDK (structured objects, extra dependency) |
| Fixed answer format (ROOT CAUSE / EVIDENCE / FIX) | Forces the model to cite evidence and makes eval scoring easier | Free-form answer |
| `MAX_STEPS = 10` already in Stage 1 | A small model can loop forever calling tools | No limit |

### Interview questions
1. **"Walk me through what happens when the model 'calls a tool'."**
   The model only generates text. Because it was fine-tuned on tool-call
   examples and we put JSON schemas in the prompt, it outputs a structured tool
   call. Ollama parses it into `tool_calls`. My loop looks up the Python
   function, runs it, appends the output as a `tool` message and calls the
   model again. The loop ends when the model replies without tool calls.
2. **"How does the agent remember what it already did?"**
   It doesn't. The model is stateless. I resend the entire message list every
   step, so memory equals the context window. That's why I truncate tool output
   and cap the number of steps.
3. **"Why not just put the logs in the prompt instead of building an agent?"**
   You don't know in advance which container or which information is needed.
   The agent chooses (list containers, then logs of the failing one, then
   inspect if logs are empty). For one fixed check, a plain script would be
   simpler and better.
