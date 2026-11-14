# Port conflict (host port already in use)

## Symptoms
- `docker compose up` or `docker start` fails with `Bind for 0.0.0.0:8080 failed: port is already allocated`.
- Or: `listen tcp 0.0.0.0:8080: bind: address already in use`.
- The container stays in status `Created` and never starts; `docker logs` is empty.

## How to confirm
- `docker inspect <container>` and look at `State.Error`; it contains the bind error.
- `docker ps` shows another container publishing the same host port, or `ss -ltnp` shows a host process on it.

## Common causes
- Two services in the same compose file publish the same host port (`"8080:80"` twice).
- An old container from a previous run still holds the port.
- A host program (another web server, a dev server) already listens on that port.

## Fix
- Change the host side of the mapping, e.g. `"8081:80"`. The container side can stay the same.
- Or stop/remove the other container (`docker rm -f <old>`) or the host process.
