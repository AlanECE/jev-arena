# Laya (Convai Innovations, Apache 2.0) servi en local, meme API que Jev : POST /v1/systemone
# PyTorch CUDA 13 (cu130) : necessaire pour les cartes RTX 50 (Blackwell). Sans GPU, mettre LAYA_DEVICE=cpu.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_NO_CACHE=1 HF_HOME=/models USE_TF=0

RUN pip install uv==0.13.0 \
    && uv pip install --system "laya[serve]==0.4.6" --torch-backend=cu130

EXPOSE 8000
CMD ["laya-serve"]
