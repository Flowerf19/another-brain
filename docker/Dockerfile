# another-brain MCP server image.
#
# The embedding model (~0.5 GB) is deliberately NOT baked into the image:
# it downloads into the model-cache volume on first boot (the compose
# service sets MODEL_DOWNLOAD_POLICY=auto / MODEL_ALLOW_NETWORK=true).
# Keeps the image small and the <1 GB local footprint in a volume the
# operator controls.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first for layer caching. The `local` extra pulls
# sentence-transformers + CPU-only torch (pyproject pins the pytorch-cpu
# index, so no CUDA wheels land in the image).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra local --no-install-project

COPY src/ ./src/

ENV PYTHONUNBUFFERED=1 \
    MODEL_CACHE_DIR=/data/models \
    MCP_HTTP_HOST=0.0.0.0

EXPOSE 1905
ENTRYPOINT ["/app/.venv/bin/python", "src/main.py"]
CMD ["serve", "--transport", "http"]
