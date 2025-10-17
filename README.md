# Music API (Dockerized)

This repository contains a Flask-based music API that caches and streams audio files. It supports downloading audio (via yt-dlp), caching tracks, and streaming them with byte-range requests.

## Files added for Docker

- `Dockerfile` - Builds a slim Python image, installs system deps (ffmpeg, libpq), installs Python deps, and runs the app with `gunicorn`.
- `entrypoint.sh` - Ensures the `/app/music` directory exists and exports `.env` variables before starting the server.
- `docker-compose.yml` - Compose service to build and run the container. It maps port `4000` and mounts `./music` to `/app/music`.
- `.dockerignore` - Files excluded from the Docker build context.
- `requirements.txt` - Pinned Python dependencies.

## Build & Run (Docker Compose)

1. Ensure you have Docker and Docker Compose installed.
2. Create or verify a `.env` file in the repository root (already present in this project). It must contain at least `API_KEY` and `NEON_CONNECTION_STRING` for the app to function.

To build and start the service:

```bash
docker compose up --build -d
```

To view logs:

```bash
docker compose logs -f
```

To stop and remove containers:

```bash
docker compose down
```

## Build & Run (Docker CLI)

Build the image:

```bash
docker build -t music-api:latest .
```

Run the container (mounting music dir and passing .env):

```bash
docker run --rm -p 4000:4000 --env-file .env -v "$(pwd)/music":/app/music --name music-api music-api:latest
```

## Notes

- The compose healthcheck calls `/api/tracks` using `X-API-Key` from `.env`. Make sure `API_KEY` is set in `.env`.
- For production, prefer secrets management instead of committing `.env` to source control.
- Adjust Gunicorn worker count in the `Dockerfile` CMD or docker-compose `command` override depending on your CPU/RAM.

If you want, I can:
- Add a simple `health` endpoint that doesn't require an API key (so Docker healthchecks don't need the key), or
- Add a Compose file variant that includes a local Postgres (for local dev) and wiring. 
