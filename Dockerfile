# syntax=docker/dockerfile:1
# Public image. Base from Docker Hub, packages from Debian and PyPI.
# No private registry and no build secrets.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="openai-dial-proxy" \
      org.opencontainers.image.description="OpenAI-compatible proxy in front of AI DIAL Core" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/sergey-zinchenko/openai-dial-proxy"

RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1001 --shell /usr/sbin/nologin appuser

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY app.py LICENSE NOTICE ./

USER 1001:1001
EXPOSE 8080

ENV TMPDIR=/tmp
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "75"]
