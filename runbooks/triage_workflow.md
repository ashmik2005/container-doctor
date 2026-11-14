# General triage workflow for a broken container

## Steps
1. `docker ps -a`: find containers that are `Exited`, `Restarting`, `Created` or `(unhealthy)`.
2. Note the exit code and look it up (137 = killed/OOM, 127 = command not found, 1 = app error, 0 = process finished).
3. `docker logs --tail 50 <container>`: the last error line usually names the cause.
4. If logs are empty, `docker inspect <container>`: `State.Error`, `State.OOMKilled`, `State.Health`, `Mounts`, `Config.Env`, `Config.Cmd`, `HostConfig`.
5. If the container is Up but unreachable, check ports: mapping, bind address, and whether anything listens.
6. Check host resources: disk space and memory.
7. Fix the root cause, not the symptom. Restarting a crash-looping container doesn't fix it.
