# Wrong command or entrypoint (exit code 127 / 126)

## Symptoms
- `exec: "pyhton": executable file not found in $PATH`.
- `OCI runtime create failed` / `exec format error`.
- Status `Created` or `Exited (127)`; `docker logs` may be empty.

## How to confirm
- `docker inspect <container>`: `State.ExitCode` 127 (command not found) or 126 (not executable), and `State.Error` holds the message.
- `Config.Cmd` / `Config.Entrypoint` show the exact command Docker tried to run.

## Common causes
- Typo in `command:` or `entrypoint:`.
- The program isn't installed in this image (e.g. `bash` in alpine, which only has `sh`).
- Script without execute permission (126) or Windows line endings in the shebang.
- Image built for another CPU architecture (`exec format error`).

## Fix
- Fix the command spelling or path, use a tool that exists in the image, `chmod +x` the script, or use the right platform image.
