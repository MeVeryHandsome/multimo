# Multimo object detector. CPU by default; docker-compose.gpu.yml switches to
# a CUDA build of PyTorch. Versions are the ones the service was run with:
# CPU torch 2.15.0 / torchvision 0.29.2, GPU torch 2.7.1 / torchvision 0.22.1 (cu126).
FROM python:3.13-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.15.0
ARG TORCHVISION_VERSION=0.29.2
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 YOLO_CONFIG_DIR=/tmp/ultralytics

RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --index-url ${TORCH_INDEX} torch==${TORCH_VERSION} torchvision==${TORCHVISION_VERSION}
COPY requirements.txt /app/requirements.txt
RUN pip install -i ${PIP_INDEX_URL} -r /app/requirements.txt
COPY app.py fusion.py rescore.py /app/

WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s CMD curl -fs localhost:8000/health || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
