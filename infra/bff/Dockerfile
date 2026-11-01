# Faultline BFF — FastAPI read layer the React UI consumes.
#
# The BFF's src.pricer imports are QuantLib-free (domain objects + the storage layer only), so this
# image drops QuantLib entirely. It installs the pinned runtime set minus the QuantLib wheel, so
# every (incl. lazy) src import resolves. (The backend image — infra/backend/Dockerfile — is also a
# plain python:3.12-slim + the QuantLib PyPI wheel; there is no conda anywhere.)
#
# Build (from repo root):
#   docker build -f infra/bff/Dockerfile -t qlp-bff .
# Run (data mounted; QLP_ROOT points at it):
#   docker run --rm -p 8088:8088 -e QLP_ROOT=/app -v "$PWD/data:/app/data" \
#       -e QLP_SERVICE_URL=http://backend:8080 qlp-bff

FROM python:3.12-slim

WORKDIR /app

# Reuse the pinned runtime set but drop the QuantLib wheel (the BFF never imports it); add the
# FastAPI service extras.
COPY requirements.txt /tmp/requirements.txt
RUN grep -v -i '^QuantLib' /tmp/requirements.txt > /tmp/req.pip.txt \
    && pip install --no-cache-dir -r /tmp/req.pip.txt fastapi==0.120.0 uvicorn==0.38.0

COPY src ./src
COPY ui ./ui
COPY configs ./configs
COPY pyproject.toml ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8088
# v1: QLP_ROOT / a local PRICER_DATA_URI point at the mounted (EFS / S3-synced) data tree.
CMD ["uvicorn", "ui.bff.app.main:app", "--host", "0.0.0.0", "--port", "8088"]
