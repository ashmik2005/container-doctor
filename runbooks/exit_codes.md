# Container exit codes reference

## Common exit codes
- `0`: the main process finished normally. For a server this is often a bug: the process ran in the background or had nothing to do.
- `1`: generic application error. Read the logs for the exception.
- `2`: misuse of a shell builtin / wrong CLI arguments.
- `125`: the docker run command itself failed (bad flag, daemon error).
- `126`: command found but not executable (permissions).
- `127`: command not found (typo in command/entrypoint, missing binary).
- `137`: SIGKILL (128+9). Usually OOM killer (check `State.OOMKilled`) or `docker kill`.
- `139`: SIGSEGV (128+11), segmentation fault in native code.
- `143`: SIGTERM (128+15), graceful stop, e.g. `docker stop`. Usually normal.

## How to use
- Exit codes above 128 mean "killed by signal (code - 128)".
- Always pair the exit code with `docker logs` and `docker inspect` State.
