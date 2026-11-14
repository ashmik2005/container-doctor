# Wrong volume / bind mount path

## Symptoms
- App fails with `FileNotFoundError` / `No such file or directory` for a config file that "should be mounted".
- The container starts, but a mounted directory appears empty.
- Error `not a directory: Are you trying to mount a directory onto a file (or vice-versa)?`.

## How to confirm
- `docker inspect <container>` and look at `Mounts`: compare `Source` (host path) and `Destination` (container path) with the path the app reads from.
- A typo in the host path makes Docker create an empty directory instead of failing.

## Common causes
- Container-side path doesn't match where the app looks (`/conf` vs `/config`).
- Relative host path resolved from a different directory than expected.
- Typo in the host file name, so Docker mounts a new empty directory.

## Fix
- Make the mount `Destination` match the path the app reads, or change the app's config path.
- Use paths relative to the compose file (`./config:/config`) and check that the host file exists.
