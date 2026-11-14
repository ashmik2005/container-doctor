# OOM killed (out of memory, exit code 137)

## Symptoms
- Container status `Exited (137)`.
- Logs stop abruptly with no error message, often in the middle of work.
- Logs may show the app was allocating memory or processing a large batch.

## How to confirm
- `docker inspect <container>` shows `State.OOMKilled: true` and `ExitCode: 137`.
- `HostConfig.Memory` shows the memory limit in bytes (e.g. 20971520 = 20 MiB).
- 137 = 128 + 9: the process received SIGKILL (signal 9) from the kernel OOM killer.

## Common causes
- `mem_limit` / `deploy.resources.limits.memory` set too low for the workload.
- A memory leak or loading a whole large file/dataset into memory.

## Fix
- Raise the memory limit in compose (`mem_limit: 512m`) if the workload is legitimate.
- Otherwise reduce memory use: stream data, smaller batches, fix the leak.
