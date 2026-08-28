FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY lingjing_harness ./lingjing_harness
COPY frontend ./frontend
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LINGJING_DATA_DIR=/data
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/xushu_recsys_harness-*.whl \
    && rm -rf /wheels \
    && addgroup --system xushu \
    && adduser --system --ingroup xushu --home /app xushu \
    && mkdir -p /data \
    && chown -R xushu:xushu /data
VOLUME ["/data"]
USER xushu
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health/ready', timeout=2).read()" || exit 1
CMD ["uvicorn","lingjing_harness.api:app","--host","0.0.0.0","--port","8765"]
