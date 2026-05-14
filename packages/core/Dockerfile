FROM python:3.12-slim AS base

WORKDIR /app

# System deps for audio + cryptography wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl build-essential libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Use uv for fast installs
RUN pip install --no-cache-dir uv

# Install dependencies first (layer cache).
# Torch pulls ~3 GB of CUDA wheels by default which we will never use
# (Silero VAD inference is CPU-bound on this image). Pin the CPU-only
# torch wheel from PyTorch's CPU index first; uv then satisfies
# `silero-vad`'s torch dep from this pre-installed version rather than
# downloading the giant CUDA variant from PyPI on the next layer.
# `--no-deps` keeps it from dragging in extras we don't want here.
COPY pyproject.toml README.md ./
# NB: torch comes in as a transitive dep of silero-vad. By default
# PyPI ships the CUDA-enabled wheel (~3 GB) even though we only need
# CPU inference. We tried switching to pytorch.org's CPU index here,
# but Zscaler corp TLS blocks that host on the user's machine. To
# slim the image when you have unrestricted egress, install torch CPU
# explicitly *before* this line:
#   RUN pip install --index-url https://download.pytorch.org/whl/cpu \
#                   --no-deps torch torchaudio
RUN uv pip install --system -e ".[dev]" || uv pip install --system -e "."

# Copy source
COPY openvox ./openvox
COPY main.py ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENVOX_DATA_DIR=/data

EXPOSE 8000
CMD ["python", "main.py"]
