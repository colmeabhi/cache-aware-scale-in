# The controller as a container image (stage 6). Build from the repo root:
#   docker build -f stage6_final/Dockerfile -t cache-aware-controller:local .
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "kubernetes>=29,<33"
COPY cacheaware /app/cacheaware
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
USER 65532:65532
CMD ["python", "-m", "cacheaware.controller"]
