# Wrong database credentials

## Symptoms
- Postgres: `FATAL: password authentication failed for user "app"`.
- MySQL: `Access denied for user 'app'@'172.18.0.3' (using password: YES)`.
- The database container is healthy; only the app fails.

## How to confirm
- DB logs show the authentication failure at the same time as the app error.
- Compare app env (`DB_PASSWORD`, `DATABASE_URL`) with DB env (`POSTGRES_PASSWORD`, `POSTGRES_USER`) in `docker inspect` and look at `Config.Env`.
- Postgres only reads `POSTGRES_PASSWORD` when the data volume is first initialised; changing it later has no effect on an existing volume.

## Fix
- Make the app's credentials match the DB's. If the volume was initialised with an old password, change it with `ALTER USER` or recreate the volume (destroys data: confirm first).
