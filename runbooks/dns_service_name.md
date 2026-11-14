# DNS / service name cannot be resolved

## Symptoms
- `socket.gaierror: [Errno -2] Name or service not known`.
- Alpine/musl: `Name does not resolve` or `bad address 'database'`.
- Postgres: `could not translate host name "database" to address`.
- Inside Docker an unknown service name can also show up as a *temporary* failure:
  `socket.gaierror: [Errno -3] Try again` or `Temporary failure in name resolution`.
  Docker's DNS cannot answer the name and the resolver reports it as "try again".

## How to confirm
- Compare the hostname in the error with the service names in docker-compose.yml. Containers reach each other by **service name** (or `container_name`) on a shared network.
- `docker inspect <container>` and look at `NetworkSettings.Networks`: both containers must share a network.

## Common causes
- Typo or wrong name: app connects to `database` but the service is called `db`.
- The two services are on different user-defined networks.
- Using `localhost` to reach another container (localhost is the container itself).

## Fix
- Use the exact service name as the host (`DB_HOST=db`), or add a network alias.
- Put both services on the same network.
