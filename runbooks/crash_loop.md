# Crash loop (container keeps restarting)

## Symptoms
- `docker ps` shows `Restarting (1) 5 seconds ago`, or status flips between Up and Restarting.
- Logs show the same startup error repeated many times.

## How to confirm
- `docker inspect <container>`: `RestartCount` is high and `HostConfig.RestartPolicy.Name` is `always` or `unless-stopped` or `on-failure`.
- The restart policy only hides the real problem: find the error that repeats in the logs.

## Common causes
- Any startup failure (bad config value, missing env var, unreachable dependency) combined with a restart policy.
- Docker backs off restarts exponentially, so the container spends most time in `Restarting`.

## Fix
- Diagnose the repeated error in `docker logs`, fix that root cause, then `docker compose up -d`.
- Restarting again will not help until the underlying error is fixed.
