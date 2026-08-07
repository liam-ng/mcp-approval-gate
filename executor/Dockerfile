# Executor image. Build context is ./executor
#
# Same two-stage venv shape as backend/Dockerfile, and for the same reason:
# pip vendors frozen msgpack/setuptools copies that Trivy flags and upgrading
# pip does not move them, so pip never reaches the runtime layer.
FROM python:3.14-slim AS builder
RUN python -m venv /opt/venv

COPY requirements.txt ./
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && rm -rf /opt/venv/lib/python*/site-packages/pip \
              /opt/venv/lib/python*/site-packages/pip-*.dist-info \
              /opt/venv/bin/pip*

FROM python:3.14-slim AS runtime
WORKDIR /srv

RUN rm -rf /usr/local/lib/python*/site-packages/pip \
           /usr/local/lib/python*/site-packages/pip-*.dist-info \
           /usr/local/lib/python*/site-packages/setuptools \
           /usr/local/lib/python*/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python*/site-packages/pkg_resources \
           /usr/local/bin/pip*

COPY --from=builder /opt/venv /opt/venv
COPY app ./app

RUN addgroup --gid 1001 executor \
    && adduser --uid 1001 --gid 1001 --disabled-password --gecos "" executor \
    && chown -R 1001:1001 /srv
USER 1001:1001

ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
# No EXPOSE and no port: this is a polling client, it serves nothing.
CMD ["python", "-m", "app.main"]
