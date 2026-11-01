# Faultline backend — ONE image for every batch runner + the pricer FastAPI service.
#
# QuantLib 1.42.1 is installed from the PyPI wheel — the SAME build the golden PVs are baselined
# against (the local py312 env installs QuantLib via pip, i.e. pypi_0, not conda-forge; the
# conda-forge `quantlib` package is C++-library-only and has no Python bindings). Using the wheel
# keeps results reproducible AND drops the conda toolchain entirely, so the image is a slim
# python-base rather than a ~1.5 GB conda image.
#
# Build (from repo root):
#   docker build -f infra/backend/Dockerfile --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t qlp-backend .
# Run a batch runner (data mounted, local mode):
#   docker run --rm -v "$PWD/data:/app/data" qlp-backend python -m src.pricer.risk_runner
# Run the pricer service:
#   docker run --rm -p 8080:8080 -v "$PWD/data:/app/data" qlp-backend \
#       uvicorn src.pricer.service:app --host 0.0.0.0 --port 8080
# AWS: set PRICER_DEPLOY_MODE=aws + PRICER_DATA_URI=s3://bucket/prefix instead of mounting data.

FROM python:3.12-slim

WORKDIR /app

# Full pinned runtime set (QuantLib wheel included) + the optional FastAPI service extras, so this
# one image runs every batch runner and the pricer service.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt fastapi==0.120.0 uvicorn==0.38.0

COPY src ./src
COPY configs ./configs
COPY pyproject.toml ./

ARG GIT_SHA=unknown
ENV PRICER_GIT_SHA=$GIT_SHA \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Default: price the full book. Override the command for other runners or the service.
CMD ["python", "-m", "src.pricer.runner"]
