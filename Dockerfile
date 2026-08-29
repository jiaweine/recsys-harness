FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS builder
WORKDIR /build
COPY pyproject.toml README.md requirements-runtime.txt ./
COPY scripts/verify_runtime_wheelhouse.py ./verify_runtime_wheelhouse.py
COPY lingjing_harness ./lingjing_harness
COPY frontend ./frontend
RUN pip wheel --no-cache-dir --constraint requirements-runtime.txt --wheel-dir /wheels . \
    && python verify_runtime_wheelhouse.py requirements-runtime.txt /wheels

FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5
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
