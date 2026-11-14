# Database not ready when the app starts (startup race)

## Symptoms
- The app container exits at startup with `connection refused` to the database host and port (e.g. `db:5432`).
- Postgres clients: `could not connect to server: Connection refused` or `the database system is starting up`.
- The database container itself is running and healthy a few seconds later.
- Restarting only the app later works fine.

## How to confirm
- Compare timestamps: the app failed seconds after start, before the DB logged `database system is ready to accept connections`.
- The compose file has `depends_on: [db]` with no healthcheck. Plain `depends_on` only waits for the DB container to *start*, not to be *ready*.

## Fix
- Add a healthcheck to the DB (`pg_isready -U postgres`) and use
  `depends_on: { db: { condition: service_healthy } }` for the app.
- Also make the app retry the connection with backoff; production systems should not rely on start order.
