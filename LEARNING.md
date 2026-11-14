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

---

## Stage 2: More tools and safety

### What we built
- New tools: `inspect_container` (a summarised `docker inspect`), `check_port`
  (who publishes the port, is anything listening, what does a TCP/HTTP probe
  return), `check_disk` (`df -h /` + `docker system df`), and
  `restart_container` (an action that needs approval).
- Safety in `tools.py`: a command allowlist inside `_run()`, a container-name
  regex, secret masking in env vars, `logs --tail` clamped to 200.
- Safety in `agent.py`: validation of unknown tools and bad arguments,
  human approval for actions, `MAX_STEPS`, output truncation, a repeated-call
  guard, an evidence guard, and an explicit `num_ctx`.
- `test_safety.py`: 6 offline tests of the safety layer.
- `scenarios/02_oom_killed/`: a container killed by a 20 MiB memory limit (exit 137).

### Safety layers (defense in depth)
Each layer assumes the others might fail:
1. **The model only picks from tools we wrote.** It can't run arbitrary
   commands. A tool's command prefix is fixed in our code; the model only fills
   in arguments.
2. **Arguments are validated.** Unknown tool → error listing the real tools.
   Missing argument → error. Made-up extra arguments are dropped. Container
   names must match Docker's naming rule `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$`, which
   also blocks *option injection* (`container="--follow"` would make
   `docker logs` hang forever).
3. **Allowlist in `_run()`.** Even a buggy tool can't run `docker rm`; only
   listed read-only prefixes are allowed. `docker restart` needs `allow_actions=True`.
4. **No shell.** `subprocess.run([...])` with a list, never `shell=True`, so
   `;`, `&&`, `$( )` mean nothing.
5. **Human approval** for anything that changes state (`NEEDS_APPROVAL`). The
   `approve` function is a parameter: the terminal asks y/n, and `eval.py`
   passes a function that always says no.
6. **Errors become messages, never crashes.** `execute_tool_call` returns
   `ERROR: ...` text. The model reads it in the next step and can correct itself.

### Context window management
- **Truncation:** tool output over 3000 chars keeps the first 25% and the last
  75%, with a `[N characters truncated]` marker. Logs put the fatal error at the end.
- **`num_ctx = 8192`:** Ollama's default context window is small. When the
  prompt is longer, Ollama **silently drops the oldest tokens**, including the
  system prompt, so the agent "forgets" its instructions with no error. Setting
  it explicitly avoids that surprise.
- **Summarised inspect:** raw `docker inspect` is ~250 lines; we send ~40.

### Lessons from testing (real observations)
- On the OOM scenario the model first answered right after `list_containers`
  ("exit 137 → probably memory"). The guess was right, but there was no
  evidence. Adding "never answer from the container list alone" to the
  **prompt did not help** (it still did it). So the rule moved into **code**:
  the *evidence guard* sends one nudge if the model tries to answer before
  using `get_logs`/`inspect_container`. **Lesson: with small models, enforce
  important rules in the loop, not in the prompt.**
- **`temperature: 0` is not full determinism for an agent:** tool output
  contains times ("Exited 38 seconds ago" vs "About a minute ago"). Different
  input tokens → different generation. Two runs on the same scenario took
  different paths.

### Design decisions
| Decision | Why | Alternative considered |
|---|---|---|
| Allowlist of command prefixes | Simple, explicit, easy to audit | Denylist (`rm`, `kill`…): you always forget something |
| Approval via an injectable `approve` function | Same loop works interactively and in automated eval | Hard-coded `input()` (would block eval) |
| Return errors to the model as text | The model can self-correct; the agent never crashes | Raise exceptions (one bad call kills the run) |
| Drop unknown args, error on missing ones | Small models add junk args often; missing args can't be guessed | Reject on any mismatch (too strict for a 3B model) |
| Truncate head 25% + tail 75% | Errors are usually at the end of logs | Keep only the head (loses the error) |
| Summarise `docker inspect` in Python | Saves ~85% of tokens, removes distractions | Send raw JSON |
| Mask secret-looking env values | Logs/env often contain passwords; the model doesn't need the value to diagnose | Send everything (fine locally, bad habit) |
| Evidence guard in code (one nudge) | The prompt rule was ignored by the 3B model | Bigger model; stronger prompt (tried, failed) |
| Repeated-call guard | Small models re-call the same tool in loops | Rely only on MAX_STEPS |
| On step limit: one final call with `tools=None` | Always produces an answer instead of nothing | Return "gave up" |

### Interview questions
1. **"How do you stop the agent from doing something destructive?"**
   Defense in depth. The model can only pick tools I wrote. Arguments are
   validated (name regex blocks injection). `_run()` has an allowlist of
   read-only command prefixes. No `shell=True`. Any state-changing tool needs
   human approval. The approval function is injectable, so eval auto-denies.
2. **"What happens if the model calls a tool that doesn't exist?"**
   `execute_tool_call` returns `ERROR: unknown tool 'x'. Available tools: ...`
   as the tool result. The model sees it in the next step and usually picks a
   real tool. Same for bad arguments. The agent never crashes on model mistakes.
3. **"What is the context window and how do you manage it?"**
   It's the maximum number of tokens the model sees at once: system prompt +
   all messages. The agent resends everything every step, so it grows. I set
   `num_ctx=8192` explicitly (Ollama silently drops the oldest tokens when
   over the limit), truncate tool output to 3000 chars keeping the tail,
   summarise `docker inspect`, and cap steps at 8.

---

## Stage 3: Runbook RAG (agentic retrieval)

### What we built
- `runbooks/`: 21 short markdown runbooks, one failure type each, all with the
  same sections: `## Symptoms`, `## How to confirm`, `## Common causes`, `## Fix`.
- `rag.py`: chunk → embed → store in ChromaDB (`python rag.py`), and `search(query)`.
- `search_runbooks(query)` tool. The **model decides when to call it and what
  to search for** (agentic RAG).
- `eval_retrieval.py`: measures retrieval quality of 3 chunking strategies.
- Agent hardening found while testing: final-answer checks, `num_predict`
  cap, and approval that can't hang.

### Key concepts

**Embedding (recap).** Text → vector of 768 numbers. Similar meaning → nearby vectors.

**Cosine similarity.** It measures the angle between two vectors and ignores
their length: `cos = (a·b) / (|a|·|b|)`. 1 means the same direction, 0 means
unrelated. Tiny example with 2-D vectors: a=(1,1), b=(2,2) → cos = 4/(√2·√8) = 1
(same meaning, different "length"); a=(1,0), b=(0,1) → cos = 0. ChromaDB is set to
`hnsw:space = cosine` and returns *distance* = 1 − similarity.

**Vector DB / HNSW.** ChromaDB stores vectors plus metadata and finds the
nearest ones fast with HNSW, a graph index for approximate nearest-neighbour
search. With 73 chunks a plain loop would also do. The DB earns its place by
persisting to disk and scaling.

**Task prefixes.** `nomic-embed-text` was trained with `search_document: ` on
stored texts and `search_query: ` on questions. Leaving them out quietly lowers
retrieval quality. It's an easy detail to miss.

**Chunking: why chunk size matters.** An embedding is one vector for the
whole chunk, a kind of "average meaning". Too big: a chunk mixes topics and
its vector matches nothing well. Too small: the chunk loses context ("restart
the container", but for which problem?).

**Our chunking: one chunk per `## ` section, prefixed with the runbook title.**
The runbooks already have meaningful sections, so we split there instead of
at an arbitrary character count. The title prefix stops every `## Fix` chunk
looking alike.

**Small-to-big retrieval.** We *search* over small chunks (precise matching)
but *return* the whole runbook (2 at most, deduplicated). The model gets
symptoms, confirmation steps and the fix together.

### Measured: chunking strategies (`eval_retrieval.py`, 20 queries)
| Chunking strategy | Chunks | hit@1 | hit@2 |
|---|---|---|---|
| whole file (1 chunk per runbook) | 21 | 19/20 | 19/20 |
| ## sections, no title | 73 | 19/20 | 19/20 |
| ## sections + title (used in rag.py) | 73 | 19/20 | 20/20 |

**Honest reading:** with runbooks this short (≤ ~1100 chars), chunking hardly
matters. The difference is 1 query out of 20, which is not statistically
meaningful. I kept sections + title because it was never worse and will
matter more if runbooks get longer. Weakness: I wrote both the runbooks and the
queries, so the vocabulary overlaps more than real user text would.

### Agentic RAG vs always-retrieve
- **Always-retrieve:** embed the user question, stuff the top runbooks into the
  prompt before the agent starts. Simple, but the question ("my web app is
  down") is vague. The useful query is the *error message*, which only exists
  after the agent has read the logs.
- **Agentic:** retrieval is a tool. The agent searches with
  `"KeyError DATABASE_URL"` *after* finding it in the logs. That's a much better query.
- **What I observed:** in 2 of my first 3 manual runs the 3B model never
  called `search_runbooks`, even though the prompt told it to. So the loop
  now sends **one** nudge ("call search_runbooks with the error you found")
  if the model tries to answer without searching. The model still chooses
  *when* and *what* to search. This makes it "agentic retrieval with a
  guardrail", and it's the honest way to describe it.

### Bugs found while testing (and what they taught)
1. **Approval prompt hung forever.** The model called `restart_container`.
   `input()` waited for a y/n on a stdin that wasn't a terminal. Fix: if
   `sys.stdin.isatty()` is false, **deny** automatically. When the human
   can't be asked, fail safe, never fail open.
2. **Runaway generation (one request ran 15+ minutes).** Ollama's server log
   showed normal requests taking 5–80 s, then one that never finished: the
   model fell into a repetition loop. Fix: `num_predict = 1024` caps output
   tokens per call. Every LLM call needs a token cap and a timeout.
3. **"Announce instead of act".** The model replied "Let's confirm this by
   checking the env vars" *without* a tool call. Since there were no tool
   calls, the loop treated it as the final answer. Fix: the `bad_format`
   check. An answer without `ROOT CAUSE` gets one nudge: "call a tool or answer
   in the format".

### Design decisions
| Decision | Why | Alternative considered |
|---|---|---|
| ChromaDB, persistent, local | Simple API, stores vectors + text + metadata, no server process | FAISS (no metadata/persistence built in), pgvector (needs Postgres), plain numpy (fine at this size, but no persistence) |
| Our own embeddings via Ollama (`embedding_function=None`) | Same embedding model for docs and queries, no second model download | Chroma's default ONNX embedder |
| Section chunks + title prefix | Natural semantic units; measured as good as or better than alternatives | Fixed 500-char windows with overlap (splits mid-sentence; needed for long unstructured docs) |
| Return whole runbooks (top 2) | Model needs symptoms + confirm + fix together; 2 runbooks ≈ 2k chars | Return only matched chunks (missing context) |
| Retrieval as a tool (agentic) | Best query is the error message found *during* investigation | Retrieve once from the user's question |
| One "search the runbooks" nudge | 3B model rarely chose to retrieve on its own | Pure agentic (tool mostly unused) or forced retrieval every step |
| `rag` imported inside `search_runbooks` | Other tools work even if ChromaDB is missing/broken | Import at top of `tools.py` |
| System prompt built from available tools | "No-RAG" eval runs must not be told about a tool they don't have | One fixed prompt |

### Interview questions
1. **"Why did you use agentic RAG instead of just retrieving up front?"**
   The user's question is vague ("web app is down"). The best search query is
   the error message, which the agent only has after reading logs. As a tool,
   retrieval happens at the right moment with a precise query. The cost: a
   small model may not call it, so I added a single nudge and I measure
   with/without in the eval.
2. **"How did you choose your chunking strategy?"**
   I chunked by markdown section with the title prefixed, then measured three
   strategies on 20 queries. All got 19/20 at hit@1; mine got 20/20 at hit@2.
   The honest conclusion: for short, well-structured docs, chunking hardly
   matters. For long docs you'd need size-bounded chunks with overlap.
3. **"What is cosine similarity and why use it for embeddings?"**
   The cosine of the angle between two vectors: dot product divided by the
   product of lengths. It compares direction (meaning) and ignores magnitude,
   so it's the standard measure for text embeddings. Chroma returns distance =
   1 − cosine similarity.
