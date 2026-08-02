# Stage 1: build the SPA
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime serving API + static SPA
FROM python:3.12-slim AS runtime
WORKDIR /srv

# Copy only the requirements first so this layer is cached until deps change.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /build/dist ./static

RUN addgroup --gid 1001 gate && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" gate \
    && mkdir -p /data && chown -R 1001:1001 /data /srv
USER 1001:1001

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
