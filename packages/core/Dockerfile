FROM python:3.12-slim AS base

WORKDIR /app

# System deps for audio + cryptography wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl build-essential libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Use uv for fast installs
RUN pip install --no-cache-dir uv

# Install dependencies first (layer cache)
COPY pyproject.toml README.md ./
RUN uv pip install --system -e ".[dev]" || uv pip install --system -e "."

# Copy source
COPY openvox ./openvox
COPY main.py ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENVOX_DATA_DIR=/data

EXPOSE 8000
CMD ["python", "main.py"]
