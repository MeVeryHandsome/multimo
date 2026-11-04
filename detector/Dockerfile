# Multimo object detector (CPU by default; set TORCH_INDEX to a CUDA wheel
# index, e.g. https://download.pytorch.org/whl/cu126, and run with GPUs for
# docker-compose.gpu.yml).
FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 YOLO_CONFIG_DIR=/tmp/ultralytics

RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --index-url ${TORCH_INDEX} torch==2.7.1 torchvision==0.22.1
COPY requirements.txt /app/requirements.txt
RUN pip install -i ${PIP_INDEX_URL} -r /app/requirements.txt
COPY app.py /app/app.py

WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s CMD curl -fs localhost:8000/health || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
