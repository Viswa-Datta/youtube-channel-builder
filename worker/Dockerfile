# Render worker: Python + FFmpeg (libass, x264) + espeak-ng + OpenCV + ONNX Runtime + Tesseract.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    KOKORO_MODEL=/models/kokoro-v1.0.onnx KOKORO_VOICES=/models/voices-v1.0.bin KOKORO_VOCAB=/models/kokoro-vocab.json \
    FACE_MODEL=/models/face_detection_yunet_2023mar.onnx PLATE_MODEL=/models/yolo-v9-t-384-license-plates-end2end.onnx \
    ESPEAK_BIN=espeak-ng WORK_DIR=/work

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg espeak-ng tesseract-ocr fonts-dejavu-core fonts-inter curl ca-certificates libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY worker/requirements.txt worker/requirements.txt
RUN pip install -r worker/requirements.txt

# Models are baked into the image (about 360 MB) so cold starts do not download them.
COPY worker/scripts/download-models.sh /tmp/download-models.sh
RUN bash /tmp/download-models.sh /models

COPY config config
COPY fixtures fixtures
COPY worker worker
WORKDIR /app/worker

# Non-root
RUN useradd -m -u 10001 worker && mkdir -p /work && chown worker /work
USER worker

HEALTHCHECK --interval=60s --timeout=10s CMD python -c "import ycb.main" || exit 1
CMD ["python", "-m", "ycb.main", "worker"]
