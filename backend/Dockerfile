# Backend image: FastAPI only — no SPA build stage, no static/ directory.
#
# main.py's _mount_spa() early-returns when app/static doesn't exist, so the
# SPA catch-all route simply never registers here and unknown paths return a
# JSON 404 instead of index.html. That's correct for the split deployment:
# the ingress sends /api, /mcp and /.well-known here and everything else to
# the frontend pod (deploy/k8s/ingress.yaml).
#
# Build context is ./backend — it no longer needs the frontend tree at all,
# so a frontend-only change can't invalidate this image's layers.
FROM python:3.12-slim AS runtime
WORKDIR /srv

# Copy only the requirements first so this layer is cached until deps change.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN addgroup --gid 1001 gate && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" gate \
    && mkdir -p /data && chown -R 1001:1001 /data /srv
USER 1001:1001

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
