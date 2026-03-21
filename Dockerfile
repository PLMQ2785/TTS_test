ARG BASE_IMAGE=nvidia/cuda:13.0.0-devel-ubuntu24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:/root/.local/bin:${PATH}"

SHELL ["/bin/bash", "-lc"]

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    ffmpeg \
    git \
    libsndfile1 \
    libsndfile1-dev \
    ninja-build \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    sox \
    libsox-fmt-all \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
RUN uv venv "${VIRTUAL_ENV}"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY install_flash_attn.sh ./install_flash_attn.sh

ARG TORCH_CUDA_ARCH_LIST=8.6
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}
ARG MAX_JOBS=2
ENV MAX_JOBS=${MAX_JOBS}

RUN uv sync --active --no-install-project
RUN chmod +x ./install_flash_attn.sh && ./install_flash_attn.sh

COPY app ./app
COPY main.py ./main.py
COPY download_models.sh ./download_models.sh

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
