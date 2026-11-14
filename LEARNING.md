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

---

## Stage 4: Broken scenarios

### What we built
- 15 scenarios in `scenarios/NN_name/`, each a `docker-compose.yml` broken in
  exactly one known way (the broken line is commented), plus `expected.json`:
  ```json
  {"question": "My web app is down. What is wrong?",     // vague, user-style
   "root_cause": "... DATABASE_URL ... not set ...",     // ground truth for humans
   "keywords": [["DATABASE_URL"], ["environment", "env var", "missing", ...]],
   "wait_seconds": 3}                                    // time for the failure to happen
  ```
- `scenario.sh`: `list`, `pull`, `up <name>`, `down`. All scenarios use compose
  project `cdoc`, so `down` removes everything by label, whichever scenario ran.

| # | Scenario | How it fails | Where the evidence is |
|---|---|---|---|
| 01 | missing_env_var | `KeyError: 'DATABASE_URL'` | logs |
| 02 | oom_killed | exit 137, `OOMKilled: true`, 20 MiB limit | inspect (logs say nothing) |
| 03 | port_conflict | `Bind for 0.0.0.0:8080 failed: port is already allocated` | inspect `State.Error` (status `Created`, no logs) |
| 04 | db_not_ready | psql `Connection refused` before Postgres is ready | logs of api + db timing |
| 05 | wrong_volume_path | `FileNotFoundError: /config/app.yaml` (mounted at `/conf`) | logs + inspect `Mounts` |
| 06 | permission_denied | `PermissionError` creating `/var/lib/uploads` as UID 1000 | logs + inspect `User` |
| 07 | dns_wrong_host | `socket.gaierror: [Errno -3] Try again` for host `database` | logs + inspect `Env` |
| 08 | crash_loop | `Restarting`, `ValueError ... 'four'` from `WORKERS` | status + logs |
| 09 | healthcheck_failing | `Up (unhealthy)`, check probes `:8080`, nginx on `:80` | inspect `Health` |
| 10 | wrong_command | `exec: "pyhton": executable file not found` (exit 127) | inspect `State.Error` (no logs) |
| 11 | nginx_config_error | `[emerg] unexpected "}" in nginx.conf:7` | logs |
| 12 | binds_localhost | Up, but bound to `127.0.0.1` inside the container | logs + `check_port` |
| 13 | wrong_db_password | `password authentication failed for user "postgres"` | logs (app and db) |
| 14 | disk_full | `[Errno 28] No space left on device` on a 1 MB tmpfs | logs + inspect `Tmpfs` |
| 15 | missing_module | `ModuleNotFoundError: No module named 'requests'` | logs |

The scenarios are spread on purpose: some only need logs, some *only* show up
in `docker inspect` (02, 03, 10), and one only shows up when you connect (12).

### Things that surprised me while building them (verified)
- **Port conflict (03): which container fails depends on start order.** In one
  run `shop` failed, not `admin`. The failed container stays `Created` with
  exit code 128 and **empty logs**. The error is only in `State.Error`. So the
  expected root cause says "one of the two".
- **Wrong command (10) also leaves empty logs.** The process never started,
  so the error is in `State.Error` and the exit code is 127.
- **DNS (07): a wrong service name gave `[Errno -3] Try again`, not "Name does
  not resolve".** Docker's embedded DNS couldn't answer and the resolver
  reported a *temporary* failure. Putting the services on an `internal`
  network didn't change it, so I reverted that. It takes about 9 s to fail
  (`wait_seconds: 12`). The DNS runbook now lists this symptom.
- **nginx reports the line *after* the mistake.** A missing `;` on line 6 is
  reported as `unexpected "}"` on line 7, because that's where the parser notices.
- **Python stdout buffering** (01): "Starting web app..." appears *after* the
  traceback in the logs. stderr is unbuffered and stdout is flushed at exit.
  Later scenarios use `flush=True`.

### Design decisions
| Decision | Why | Alternative considered |
|---|---|---|
| Public images only (python/nginx/postgres alpine), app code inline in `command:` | No image builds, fast setup, each scenario fully visible in one file | Dockerfiles per scenario (more realistic, slower, more to explain) |
| One fixed compose project name `cdoc` | `down` can clean up any scenario by label; no leftovers between eval runs | Per-scenario project names |
| Vague user-style questions | Tests diagnosis, not reading the answer from the question | Questions that name the symptom precisely |
| Keyword groups in `expected.json` | Simple, transparent, deterministic scoring (see Stage 5) | LLM-as-judge |
| `wait_seconds` per scenario | Healthcheck/DNS/crash-loop failures need time to appear | One global sleep (too short for some, wasteful for others) |

### Interview questions
1. **"How do you know each scenario is broken in exactly one way?"**
   Each compose file differs from a working setup by one commented line. I
   started every scenario and recorded status, exit code, `State.Error` and the
   last log lines (table above) before running the agent on it.
2. **"Why do some scenarios have empty logs?"**
   If the container process never starts (port conflict, command typo), there
   is nothing to log. The error comes from Docker/runc and is stored in
   `State.Error`. That's why `inspect_container` exists, and why a logs-only
   agent would fail those cases.
3. **"What's the weakness of writing your own test scenarios?"**
   I wrote the scenarios, runbooks and keywords myself, so they share
   vocabulary and my blind spots. Real failures are messier: multiple causes,
   noisy logs, other containers running. It's a benchmark of the method, not
   proof the agent works in production.

---

## Stage 5: Evaluation

### What we built
- `eval.py`: for each of the 15 scenarios and each mode (`no_rag`, `rag`):
  start the scenario fresh (`scenario.sh up`), run the agent with the
  scenario's question and an approval function that **denies everything**,
  score the final answer, and record steps, tool calls, whether runbooks were
  used, number of nudges and wall time. It saves results after every run, so
  a crash at run 25 still leaves 24 results.
- `results/eval_results.md` / `.json`: the table (committed).
- `eval_logs/<scenario>__<mode>.json`: full message history of every run
  (git-ignored, but that's where you check *why* a run failed).

### Scoring method
Each scenario has keyword **groups**, e.g. for the missing env var:
`[["DATABASE_URL"], ["environment", "env var", "missing", ...]]`.
The answer is **correct** if every group has at least one keyword in it, as
a whole word and case-insensitive. Groups force the answer to name *what*
(DATABASE_URL) **and** *why* (missing env), so naming only one isn't enough.

**Whole-word matching** was a bug fix before the first run. Plain substring
matching made `"80"` match inside `"8080"` and `"/conf"` match `"/config"`, so
wrong answers would have scored as correct.

**Keywords never appear in the question.** For example, scenario 07's question
says "database", so "database" isn't a keyword (it uses `DB_HOST`, `hostname`,
`resolve`). Otherwise echoing the question would score.

### Weaknesses (say these before the interviewer does)
- **False positives:** an answer that lists many possible causes can hit all
  groups without committing to the right one. A keyword in a wrong context
  ("it is NOT a permission problem") also counts.
- **False negatives:** a correct answer in other words ("the variable isn't
  defined") can miss the list.
- **Same author** for scenarios, runbooks and keywords, so the vocabulary
  overlaps and the RAG gain is probably optimistic.
- **Small sample:** 15 scenarios × 1 run per mode. One flipped result moves a
  mode by ~7 percentage points. Runs are not fully deterministic (timestamps
  in tool output), so a re-run can differ.
- **Nudges blur the comparison:** in `rag` mode the loop may add a
  "search the runbooks" nudge, which also gives the model one extra turn.

### Why not LLM-as-judge?
A judge model reading (expected root cause, answer) handles paraphrases
better. But the only judge available locally is the same 3B model, which would
grade its own family's output and add its own errors. Keywords are dumb but
**transparent and deterministic**: anyone can check a score by reading the
answer. Improvement: a stronger judge model plus spot-checking by a human.

### Run v1 results (measured by eval.py, `results/eval_results_v1.md`)
| Mode | Correct (keywords) | Avg steps | Avg tool calls | Avg seconds |
|---|---|---|---|---|
| no_rag | 9/15 | 4.1 | 2.4 | 35.6 |
| rag | 12/15 | 7.7 | 4.7 | 131.3 |

Other measured facts from v1: in `rag` mode the model called
`search_runbooks` in 13 of 15 runs, and 5 runs hit the step limit (none did
in `no_rag`). Nudges: 11 in total in `no_rag`, 35 in `rag`.

### What the manual audit of v1 found (`results/manual_audit_v1.md`)
- **Keyword scoring was too generous without RAG.** 3 of the 9 "correct"
  no_rag answers were wrong or only partly right. One example: the root cause
  said "infinite loop" and "memory" appeared only in the fix.
- **RAG helped most where the error needs interpretation:** startup race (04),
  mount path (05), DNS (07), bind address (12). Those are the cases where the
  raw error message doesn't name the cause.
- **RAG also hurt once (11):** a misleading runbook ("exits immediately") led
  the model away from a correct nginx diagnosis. Retrieval adds noise as well as knowledge.
- **The eval found a bug in the agent (13 rag):** the model gave a correct
  answer, a nudge followed, it replied with an empty message, and the loop
  accepted the empty message as final. **The nudges could destroy a good
  answer.** Fixed for v2 by keeping the last answer that contains
  `ROOT CAUSE` as a fallback.
- **Fixes are often poor** even when the root cause is right: restart
  suggestions, a backwards mount fix, `apk add requests`.

### Run v2 (after the fix): the headline results
| Mode | Correct (keywords) | Avg steps | Avg seconds | Searched runbooks | Hit step limit |
|---|---|---|---|---|---|
| no_rag | 8/15 | 4.3 | 38 | 0/15 | 0/15 |
| rag | 12/15 | 7.9 | 126 | 15/15 | 6/15 |

Measured facts from comparing v1 and v2 (`results/*.json`):
- **The fallback fix fired in 2 v2 runs** (08 rag, 11 rag), and both scored
  correct. It did **not** fire in 13 rag. That run was correct in v2 because
  the model behaved differently, not because of the fix. Don't claim the
  fix "fixed scenario 13".
- **Stability:** no_rag gave the same result in 14/15 scenarios across runs,
  rag in only 11/15. Longer runs have more places to diverge.
- **Correct in both runs:** no_rag 8/15, rag 10/15. That's the most
  conservative comparison.

### The retrieval failure (an interview gold story)
Whenever `exits_immediately.md` was the top search result, the answer was
wrong (2/2). In both runs the model searched for `"Exited (1)"`, the container
*status*, not the error message. Embeddings capture meaning, not exact
tokens: "Exited (1)" and "Exited (0)" are almost the same vector, so the
runbook about clean exit-0 shutdowns ranked first, and the model trusted it.
- Fix ideas (not applied, so the test set doesn't leak into the design):
  **hybrid search** (BM25 keyword score + vector score) so exact tokens like
  `(1)`, `137`, `ENOSPC` count; or have `search_runbooks` reject
  status-only queries and ask for the error line.
- Lesson: **RAG adds noise as well as knowledge.** It needs its own
  evaluation (`eval_retrieval.py`) *and* end-to-end evaluation, because good
  retrieval on my hand-written queries didn't predict the model's own bad queries.

### Interview questions
1. **"How did you evaluate the agent, and what are the weaknesses?"**
   15 scenarios with known root causes, each run with and without the runbook
   tool, scored by keyword groups that must all match. v2: 8/15 without RAG,
   12/15 with. Weaknesses: keyword scoring is generous (a hand audit found
   symptom-restating answers counted as correct), the sample is tiny, results
   flip between runs, and I wrote the scenarios, runbooks and keywords myself.
2. **"Did RAG help?"**
   Yes, on this benchmark: 12 vs 8 in v2, 12 vs 9 in v1. It helped most where
   the error message doesn't name the cause (startup race, mount path, DNS,
   bind address: rag was right in both runs, no_rag wrong in both). It cost
   ~3.3× wall time and misled the model twice when it searched with a status
   instead of an error message.
3. **"Why did you run the evaluation twice?"**
   v1 revealed a loop bug, so I fixed it and re-ran everything instead of
   patching one number. Keeping v1 also measured run-to-run variance, which
   turned out to be large for the RAG mode (4 of 15 flipped).

---

## Stage 6: Polish

### What we built
- `README.md`: overview, a real (condensed) run from the eval, mermaid
  architecture diagram, setup, usage, safety summary, RAG summary with the
  chunking table, eval results generated from `results/*.json`, and limitations.
- The Streamlit UI was skipped (deadline). If asked: "`run_agent` already
  returns the full message list and takes a `verbose` flag, so a UI would
  just render the messages; the loop doesn't change."

### Design decisions
| Decision | Why | Alternative considered |
|---|---|---|
| README tables generated from result JSON, not typed | No transcription errors; every number is traceable to `eval.py` | Hand-written table |
| Real transcript as the README example | Shows the guardrails honestly, including the model's mistakes | A clean, idealised example |
| Keep v1 results and both audits in the repo | Shows the bug was found by evaluation, and the variance between runs | Only publish the best run |

---

## 30-second pitch (memorise the shape, not the words)
"Container Doctor is a local AI agent that diagnoses broken Docker containers.
I wrote the tool-calling loop by hand, without LangChain, so I could see and
control every step. It uses a 3B model on CPU via Ollama, six Docker tools
with an allowlist and human approval for anything that changes state, and
agentic RAG over 21 runbooks in ChromaDB. I built 15 deliberately broken
compose scenarios and an eval harness. With runbooks it got 12 of 15
diagnoses right versus 8 of 15 without, at about 3× the time. The
interesting part was what broke: the small model ignored prompt rules, so I
moved them into code; the eval caught a bug where a nudge erased a correct
answer; and semantic search failed on exit codes, which is why hybrid search
would be my next step."

---

## Stage 7: REST API (FastAPI)

Added after the main build, for roles that ask for "hands-on experience with
REST APIs" (e.g. the Sarvam AI FDSE internship).

### What we built
- `api.py`: a FastAPI app with 5 endpoints:
  | Method + path | What it does | Success code |
  |---|---|---|
  | `GET /health` | Is Ollama up with the model pulled? Is Docker up? | 200, or **503** if degraded |
  | `GET /containers` | All containers as JSON | 200 |
  | `POST /diagnoses` | Start a diagnosis, return a job id immediately | **202 Accepted** + `Location` header |
  | `GET /diagnoses/{id}` | Job status: queued / running / done / failed, plus the result | 200, or **404** |
  | `GET /diagnoses` | Recent jobs, newest first | 200 |
- `test_api.py`: 8 offline tests using FastAPI's `TestClient` and a fake agent.
- Interactive docs at `/docs`, generated automatically from the code (OpenAPI).

### Key concepts
**REST in one paragraph.** Things are *resources* with URLs (`/diagnoses/8bdc…`),
and HTTP *methods* say what to do with them: `GET` reads (safe, repeatable),
`POST` creates. *Status codes* tell the client what happened without reading
the body: 2xx success, 4xx the client's fault, 5xx the server's fault.

**Async job pattern (202 + polling).** A diagnosis takes 1–3 minutes on CPU.
Holding a request open that long breaks on client, proxy and load-balancer
timeouts. So `POST` answers in milliseconds with `202 Accepted` ("I took the
job, it's not done") and a `Location` header. The client polls that URL. The
alternatives are webhooks (the server calls the client back) and streaming
(Server-Sent Events / WebSockets, which push each agent step live).

**Pydantic validation.** Request bodies are declared as classes
(`question: str = Field(min_length=3, max_length=500)`). FastAPI checks every
request against them and returns **422** with a precise error before my code
runs. The same classes generate the `/docs` page.

**Why `def` and not `async def` for the endpoints.** The agent makes
blocking calls (Ollama HTTP, `subprocess`). In an `async def` endpoint a
blocking call freezes the whole event loop, and every other request with it.
Plain `def` endpoints run in FastAPI's thread pool, so blocking is fine.

**One worker thread.** `ThreadPoolExecutor(max_workers=1)`: the CPU can only
run one model call at a time anyway. Extra jobs wait as `queued` instead of
fighting over the CPU and all getting slow.

**Lock around the job dict.** The worker thread writes job status while
request threads read it. The lock means a reader never sees a half-updated job.

**No human in an API call.** `approve` always returns False, so state-changing
tools (restart) are refused. Same rule as eval: if you can't ask, deny.

### Design decisions
| Decision | Why | Alternative considered |
|---|---|---|
| FastAPI | Type hints → validation + OpenAPI docs for free; the standard Python API framework now | Flask (no built-in validation/docs) |
| 202 + polling | Simple for any client (curl works); survives timeouts | Synchronous POST (times out); SSE streaming (nicer UX, more code) |
| In-memory job dict | Zero dependencies; enough for one user on one machine | Redis / a database (needed for restarts and multiple server processes) |
| Single worker | Matches the hardware: one model on one CPU | More workers (they'd just slow each other down) |
| `/health` returns 503 when degraded | Load balancers and monitors understand status codes, not JSON text | Always 200 with a status field |
| Transcript only with `?include_messages=true` | Responses stay small; the full message list is available for debugging | Always include it |
| Bind to 127.0.0.1 (uvicorn default) | The API has **no authentication** and can read the Docker host | Expose on 0.0.0.0 (would need auth first) |

### Limitations (say them before you're asked)
No authentication, so it's local only. Jobs are lost on restart and the dict
grows without bound. No cancel endpoint. One server process only (each
process would have its own job dict).

### Interview questions
1. **"Why does POST /diagnoses return 202 and not 200 or 201?"**
   201 Created implies the resource is ready. 200 would mean "here is your
   result". The diagnosis isn't finished; it has only been accepted for
   processing, which is exactly what 202 means. The `Location` header tells
   the client where to poll.
2. **"What happens if two users submit at the same time?"**
   Both get a job id immediately. The executor has one worker, so the second
   job waits as `queued` and starts when the first finishes. A lock protects
   the shared job dict. With more hardware I'd add workers, and move jobs to
   Redis so several server processes can share them.
3. **"How would you make this production-ready?"**
   Authentication (API keys), a persistent job store (Redis/Postgres), a
   cancel endpoint and job expiry, streaming the agent's steps over SSE,
   rate limiting, structured logs, and running it in a container with
   read-only access to the Docker API through a proxy instead of the full socket.
