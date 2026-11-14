# Permission denied inside the container

## Symptoms
- `PermissionError: [Errno 13] Permission denied: '/var/lib/app/data.txt'`.
- `mkdir: can't create directory '/data/x': Permission denied`.
- Postgres/nginx: `could not open file ... Permission denied`.

## How to confirm
- `docker inspect <container>` and look at `Config.User`: the container runs as a non-root UID (e.g. `1000:1000`).
- The target directory is owned by root in the image, or a bind-mounted host directory is owned by another UID.

## Common causes
- `user:` set in compose (good for security) but writable directories still owned by root.
- Bind mount from host where the host UID differs from the container UID.
- SELinux labels on the host (on Fedora/RHEL, fix with `:z` on the mount).

## Fix
- Write to a directory the user owns: `chown` it in the Dockerfile (`RUN chown -R 1000:1000 /var/lib/app`) or use a named volume initialised with the right owner.
- Match the container UID to the host directory owner.
- Avoid "fixing" it by running as root unless you understand the trade-off.
