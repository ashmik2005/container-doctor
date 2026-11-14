# Healthcheck failing (container unhealthy)

## Symptoms
- `docker ps` shows `Up 2 minutes (unhealthy)`.
- Dependent services with `condition: service_healthy` never start.
- The app itself may be working fine.

## How to confirm
- `docker inspect <container>` shows `State.Health.Status: unhealthy` and `State.Health.Log` with the output of each failed check (e.g. `Connection refused`, `wget: can't connect`, `curl: not found`).
- Compare the healthcheck test command with what the app really does: port, path, tool availability.

## Common causes
- Healthcheck probes the wrong port or path (app listens on 80, check hits 8080).
- The check uses a tool that isn't in the image (`curl` missing in alpine images; use `wget`).
- Too short `start_period` for a slow-starting app.

## Fix
- Correct the healthcheck `test:` to the real port/path, use a tool present in the image, or increase `start_period`/`retries`.
