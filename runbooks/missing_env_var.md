# Missing environment variable

## Symptoms
- Container exits right after start, status `Exited (1)`.
- Python: `KeyError: 'DATABASE_URL'` from `os.environ[...]`.
- Node: `undefined` config values, or messages like `Missing required env var X`.
- Postgres image: `Database is uninitialized and superuser password is not specified`.

## How to confirm
- `docker logs <container>` shows the variable name in the error.
- `docker inspect <container>` and look at `Config.Env`: the variable is absent.

## Common causes
- The `environment:` block in compose is missing the variable or has a typo in its name.
- An `env_file:` points to a file that doesn't contain the variable.
- The variable is set on the host shell but never passed into the container.

## Fix
- Add it under `environment:` in docker-compose.yml (or to the `env_file`), then `docker compose up -d` to recreate the container.
- Make the app fail with a clear message naming the missing variable.
