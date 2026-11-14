# Read-only file system

## Symptoms
- `OSError: [Errno 30] Read-only file system: '/app/cache'`.

## How to confirm
- `docker inspect`: `HostConfig.ReadonlyRootfs: true`, or a mount with `RW: false` (`:ro` in compose).

## Fix
- Keep the root filesystem read-only (good security) but add a writable `tmpfs` or volume for the paths the app writes to, or remove `:ro` from that mount.
