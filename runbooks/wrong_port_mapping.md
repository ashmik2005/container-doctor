# Wrong port mapping (container port mismatch)

## Symptoms
- Container `Up`, logs healthy, but the host port refuses or resets connections.
- `docker ps` shows `0.0.0.0:8080->8080/tcp` while the app logs say it listens on port 80 (or vice versa).

## How to confirm
- Compare the container side of the mapping (`HOST:CONTAINER`) with the port the app logs it listens on.
- `docker inspect` and look at `NetworkSettings.Ports` and `Config.ExposedPorts`.

## Fix
- Map to the port the app really uses, e.g. `"8080:80"` for nginx, or change the app's listen port.
