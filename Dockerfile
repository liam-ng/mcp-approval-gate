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

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "pydantic>=2.7" \
    "pydantic-settings>=2.3" "authlib>=1.3" "itsdangerous>=2.2" \
    "httpx>=0.27" "boto3>=1.34" "python-ulid>=2.6"

COPY backend/app ./app
COPY --from=frontend /build/dist ./static

RUN addgroup --gid 1001 gate && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" gate \
    && mkdir -p /data && chown -R 1001:1001 /data /srv
USER 1001:1001

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
