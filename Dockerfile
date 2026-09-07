# Face -> reverse-image-search -> blockchain pipeline.
# Heavy artifacts (InsightFace models, solc) are pre-fetched at build time so
# `docker run` works without warmup downloads (runtime internet is still
# required for the reverse-image search itself).
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    INSIGHTFACE_HOME=/opt/insightface

# system libs needed by opencv / onnxruntime / scipy wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 libgomp1 libgfortran5 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# pre-fetch: face models (~300MB) and the solidity compiler
RUN python -c "from insightface.app import FaceAnalysis; a = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider']); a.prepare(ctx_id=0, det_size=(640, 640))"
RUN python -c "import solcx; solcx.install_solc('0.8.24')"

COPY facefinder/ ./facefinder/
COPY contracts/ ./contracts/
COPY samples/ ./samples/

ENTRYPOINT ["python", "-m", "facefinder"]
CMD ["run", "--input", "samples/obama.jpg"]