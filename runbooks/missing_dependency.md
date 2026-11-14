# Missing library / module in the image

## Symptoms
- Python: `ModuleNotFoundError: No module named 'requests'`.
- Node: `Error: Cannot find module 'express'`.
- Shared libraries: `error while loading shared libraries: libpq.so.5`.

## How to confirm
- `docker logs` shows the missing module name right after start.
- The image was built without installing dependencies (no `pip install -r requirements.txt`), or a slim base image lacks a system library.

## Fix
- Install the dependency in the Dockerfile (`RUN pip install -r requirements.txt`) and rebuild the image, or use an image that includes it.
