# Bad image name or tag

## Symptoms
- `manifest for nginx:1.99 not found: manifest unknown`.
- `pull access denied for myapp, repository does not exist or may require 'docker login'`.
- `docker compose up` stops before creating the container, so `docker ps -a` doesn't list it.

## How to confirm
- `docker pull <image:tag>` reproduces the error.
- Check the tag exists on the registry (Docker Hub tags page).

## Common causes
- Typo in image name or tag; a tag that was never published or was deleted.
- Private image without `docker login`.
- A local-only image name used on a machine where it was never built.

## Fix
- Correct the tag (use an existing version), or `docker login` for private registries.
- For local images, add a `build:` section or run `docker build -t <name> .` first.
