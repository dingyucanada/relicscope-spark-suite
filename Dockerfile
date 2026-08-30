# The official Python slim image is multi-architecture and provides linux/arm64.
# Keep the patch version fixed so Spark A/B build the same application image.
ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm
FROM ${PYTHON_IMAGE}

ARG APP_UID=10001
ARG APP_GID=10001
ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG RELICSCOPE_GIT_COMMIT=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    RELICSCOPE_DATA_DIR=/var/lib/relicscope \
    RELICSCOPE_HOST=0.0.0.0 \
    RELICSCOPE_PORT=8088 \
    RELICSCOPE_GIT_COMMIT=${RELICSCOPE_GIT_COMMIT}

RUN test "${APP_UID}" -gt 0 && test "${APP_GID}" -gt 0 \
    && groupadd --gid "${APP_GID}" relicscope \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
       --home-dir /opt/relicscope --create-home \
       --shell /usr/sbin/nologin relicscope

LABEL ai.relicscope.app.uid="${APP_UID}" \
      ai.relicscope.app.gid="${APP_GID}" \
      ai.relicscope.architecture="linux-arm64" \
      org.opencontainers.image.revision="${RELICSCOPE_GIT_COMMIT}"

WORKDIR /opt/relicscope

COPY requirements.lock ./requirements.lock
RUN python -m pip install \
      --index-url "${PYPI_INDEX_URL}" \
      --retries 10 \
      --requirement requirements.lock

COPY --chown=relicscope:relicscope app ./app
COPY --chown=relicscope:relicscope data ./data

RUN install -d -o relicscope -g relicscope -m 0750 \
      /var/lib/relicscope /var/lib/relicscope/uploads /opt/relicscope/runtime

USER ${APP_UID}:${APP_GID}

EXPOSE 8088

HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=4 \
  CMD python -c "import os,urllib.request; p=os.getenv('RELICSCOPE_PORT','8088'); urllib.request.urlopen('http://127.0.0.1:'+p+'/health/ready',timeout=3).read()" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088", "--no-access-log"]
