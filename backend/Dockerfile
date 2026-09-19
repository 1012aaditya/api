# The DocuParse API and worker.
#
# One image runs both: the worker is the same code with a different command,
# so there is no chance of the two drifting apart in a way that only shows up
# once a job is claimed.
#
# Built from the repository root, because the worker's entry point lives in
# scripts/ rather than backend/:
#
#   docker build -f backend/Dockerfile -t docuparse-api .

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, in their own layer, so that editing application code
# does not reinstall the whole tree on every rebuild. Every dependency ships
# a manylinux wheel, so no compiler is needed here.
COPY backend/pyproject.toml ./backend/pyproject.toml
RUN python - <<'PY' > /tmp/requirements.txt && pip install --no-cache-dir -r /tmp/requirements.txt
import tomllib

with open("backend/pyproject.toml", "rb") as handle:
    print("\n".join(tomllib.load(handle)["project"]["dependencies"]))
PY

COPY backend ./backend
COPY scripts ./scripts

# Installed without dependencies: they are already in the layer above, and
# resolving them again would defeat the cache.
RUN pip install --no-cache-dir --no-deps ./backend

COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Documents are customer financial data. Nothing here runs as root, and the
# storage directory is a volume so a container restart does not discard
# anything that has not been purged yet.
RUN useradd --create-home --uid 10001 docuparse \
    && mkdir -p /app/storage \
    && chown -R docuparse:docuparse /app
USER docuparse

ENV STORAGE_LOCAL_PATH=/app/storage
WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
