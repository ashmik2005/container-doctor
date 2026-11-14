# Disk full / no space left on device

## Symptoms
- `OSError: [Errno 28] No space left on device`.
- `write error: No space left on device`, database refusing writes, image pulls failing.

## How to confirm
- Host: `df -h` on `/var/lib/docker` shows 100% use; `docker system df` shows space used by images, containers, volumes, build cache.
- Inside the container the full filesystem may be a small `tmpfs` or volume: `docker inspect` and look at `HostConfig.Tmpfs` / `Mounts` for size limits like `size=1m`.

## Common causes
- Unbounded log files or data written into the container.
- A size-limited tmpfs or volume that's too small for the workload.
- Years of unused images/build cache on the host.

## Fix
- Increase the tmpfs/volume size, or write large data to a proper volume.
- Free host space: `docker image prune`, `docker builder prune` (destructive: confirm first).
- Configure log rotation (`max-size`, `max-file` logging options).
