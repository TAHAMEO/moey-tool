# syntax=docker/dockerfile:1.7
#
# CLIPFORGE backend: the API, both job workers and the `clipforge` CLI share this image.
# Ubuntu 24.04 matches the environment the media pipeline is tested on
# (FFmpeg 6.1 with libass/libx264, Python 3.12, pg_dump 16).
#
# Building behind a TLS-inspecting proxy: pass its CA certificate as a build secret,
#   docker build --secret id=extra_ca,src=/path/to/proxy-ca.pem ...
# It is used by the build stage only and never copied into the final image.

ARG UBUNTU_IMAGE=ubuntu:24.04

# ---------------------------------------------------------------- build stage
FROM ${UBUNTU_IMAGE} AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv python3.12-dev build-essential git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=extra_ca,required=false \
    cp /etc/ssl/certs/ca-certificates.crt /tmp/build-ca.pem \
 && if [ -f /run/secrets/extra_ca ]; then cat /run/secrets/extra_ca >> /tmp/build-ca.pem; fi
ENV SSL_CERT_FILE=/tmp/build-ca.pem \
    PIP_CERT=/tmp/build-ca.pem \
    GIT_SSL_CAINFO=/tmp/build-ca.pem \
    UV_NATIVE_TLS=1 \
    UV_PYTHON=/usr/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

ARG UV_VERSION=0.11.32
RUN python3.12 -m venv /opt/uv && /opt/uv/bin/pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app/backend
# Dependencies first (cached unless the lock file changes), then the application.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
COPY backend/vendor-wheels/ ./vendor-wheels/
RUN /opt/uv/bin/uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN /opt/uv/bin/uv sync --frozen --no-dev

# -------------------------------------------------------------- runtime stage
FROM ${UBUNTU_IMAGE} AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.12 ca-certificates tini curl \
      ffmpeg postgresql-client-16 \
      libegl1 libgles2 libgl1 libglib2.0-0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 clipforge \
 && useradd --system --uid 10001 --gid clipforge --home-dir /home/clipforge --create-home --shell /usr/sbin/nologin clipforge \
 && mkdir -p /data \
 && chown clipforge:clipforge /data

COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/backend /app/backend

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLIPFORGE_ENV=production \
    CLIPFORGE_DATA_DIR=/data \
    HOME=/home/clipforge \
    MEM0_DIR=/data/mem0 \
    MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /app/backend
USER clipforge
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["clipforge", "api", "--host", "0.0.0.0", "--port", "8000"]
