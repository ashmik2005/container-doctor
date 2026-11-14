# App listens on 127.0.0.1 inside the container (unreachable from host)

## Symptoms
- Container is `Up`, logs look healthy (`Serving HTTP on 127.0.0.1 port 8000`), port is published (`0.0.0.0:8000->8000/tcp`).
- From the host, `curl localhost:8000` fails: `Connection reset by peer` or empty reply.

## How to confirm
- Logs show the bind address `127.0.0.1` or `localhost`, not `0.0.0.0`.
- Docker forwards host traffic to the container's network interface (eth0); a server bound only to the container's loopback never receives it.

## Fix
- Bind to all interfaces: `--host 0.0.0.0` (Flask/uvicorn), `--bind 0.0.0.0` (python http.server), `HOST=0.0.0.0` (many Node apps).
