# Backend image: FastAPI only — no SPA build stage, no static/ directory.
# Build context is ./backend
FROM python:slim AS runtime
WORKDIR /srv

# Copy only the requirements first so this layer is cached until deps change.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Create a non-root user and group with UID 1001 and GID 1001
RUN addgroup --gid 1001 gate && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" gate \
    && mkdir -p /data && chown -R 1001:1001 /data /srv
USER 1001:1001

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
