# DishIt serving API + static frontend.
#
# The image carries no database. In production the app runs DISHIT_DB_MODE=replica
# and materialises an embedded libSQL replica from Turso onto the mounted volume at
# boot, so the image stays the same whatever the data does.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependencies first, so edits to application code don't re-resolve the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./backend/
COPY frontend/ ./frontend/

# backend/app/db.py resolves paths from the repository root, two parents up from
# backend/app — /app here, which is why the tree is laid out this way.
ENV PATH="/app/.venv/bin:$PATH" \
    DISHIT_DB_MODE=replica \
    DISHIT_REPLICA_PATH=/data/replica.db \
    DISHIT_SYNC_INTERVAL=60

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8080"]
