# Backend image: FastAPI only — no SPA build stage, no static/ directory.
# Build context is ./backend

# Builder stage: pip installs into a venv here and never reaches the runtime
# layer. pip vendors frozen copies of msgpack and setuptools (see
# pip/_vendor/vendor.txt) that Trivy reports as vulnerable, and upgrading pip
# does not move them — the only fix is to keep pip out of the shipped image.
FROM python:3.14-slim AS builder
RUN python -m venv /opt/venv

# Copy only the requirements first so this layer is cached until deps change.
COPY requirements.txt ./
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && rm -rf /opt/venv/lib/python*/site-packages/pip \
              /opt/venv/lib/python*/site-packages/pip-*.dist-info \
              /opt/venv/bin/pip*

FROM python:3.14-slim AS runtime
WORKDIR /srv

# The base image ships its own pip alongside the interpreter; the venv copied
# in below carries no installer, so nothing here needs one.
RUN rm -rf /usr/local/lib/python*/site-packages/pip \
           /usr/local/lib/python*/site-packages/pip-*.dist-info \
           /usr/local/lib/python*/site-packages/setuptools \
           /usr/local/lib/python*/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python*/site-packages/pkg_resources \
           /usr/local/bin/pip*

# Both stages share the base image, so the venv's interpreter symlinks and
# pyvenv.cfg still resolve to /usr/local here.
COPY --from=builder /opt/venv /opt/venv

COPY app ./app

# Create a non-root user and group with UID 1001 and GID 1001
RUN addgroup --gid 1001 gate && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" gate \
    && mkdir -p /data && chown -R 1001:1001 /data /srv
USER 1001:1001

# PATH puts the venv first so `uvicorn` below resolves to /opt/venv/bin.
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
