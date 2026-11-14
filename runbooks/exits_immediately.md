# Container exits immediately with code 0

## Symptoms
- Status `Exited (0)` seconds after start; no error in logs.

## How to confirm
- `docker inspect` and look at `Config.Cmd`: the command is a one-shot task (a script that finishes) or starts a daemon in the background.

## Common causes
- A container lives only as long as its main process (PID 1). If it finishes or forks to the background, the container stops.
- Running `service nginx start` instead of `nginx -g 'daemon off;'`.
- Using a base image like `alpine` whose default command is `sh`, which exits when there is no terminal.

## Fix
- Run the main process in the foreground. For a one-shot job, exit 0 is correct behaviour.
