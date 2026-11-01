# Pricer batch orchestration — Kubernetes manifests

The current container story is the **qlp-backend** image
([../backend/Dockerfile](../backend/Dockerfile) — `python:3.12-slim` + the QuantLib **PyPI wheel**,
one multi-entrypoint image for every runner and the pricer service), wired with the BFF + UI in the
root [docker-compose.yml](../../docker-compose.yml) (local) and
[docker-compose.aws.yml](../../docker-compose.aws.yml) (EC2 + ECR). Cloud design:
[docs/design/AITrade_aws_deployment_design.md](../../docs/design/AITrade_aws_deployment_design.md);
operating: [deployment guide](../../docs/guides/deployment_guide.md).

> **The old conda `Dockerfile` here was removed (2026-07-27).** It installed `quantlib` from
> conda-forge, which is C++-library-only (no Python bindings), so `import QuantLib` never worked in
> it — it was superseded by the working wheel-based `infra/backend/Dockerfile`. These manifests now
> run the **qlp-backend** image.

This tree keeps a **pure-Kubernetes batch path** as an alternative to the EC2 + compose substrate the
AWS design targets — useful if you already run a cluster.

## Contents
- [`k8s/eod-cronjob.yaml`](k8s/eod-cronjob.yaml) — weekday EOD full-portfolio CronJob + a `pricer-data` PVC.
- [`k8s/adhoc-job.yaml`](k8s/adhoc-job.yaml) — one-shot Job template (single asset class / date).

CI lives at [`.github/workflows/pricer-ci.yml`](../../.github/workflows/pricer-ci.yml): ruff +
`mypy --strict` + pytest on every push/PR.

## Build & run
```bash
# from repo root — tag with the git SHA so the running code is identifiable
docker build -f infra/backend/Dockerfile \
  --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
  -t qlp-backend:$(git rev-parse --short HEAD) .

# price the full portfolio (mount data in/out; the image CMD is `python -m src.pricer.runner`)
docker run --rm -v "$PWD/data:/app/data" qlp-backend:latest \
  python -m src.pricer.runner --valuation-date 2026-07-10
```

## Deploy (EOD batch on K8s)
```bash
# push the SHA-tagged image to your registry, set REGISTRY in the manifests, then:
kubectl apply -f infra/pricer/k8s/eod-cronjob.yaml
```
The manifests use `command:` (the full `python -m src.pricer.runner …`), because the qlp-backend image
sets `CMD` rather than `ENTRYPOINT`. Pin the image to a **git-SHA tag** (not `latest`) in production so
the deployed code matches the `git_sha` recorded in every result's provenance. For S3-backed data, set
`PRICER_DEPLOY_MODE=aws` + `PRICER_DATA_URI=s3://…` (via a ConfigMap/Secret) instead of the PVC mount.

## Dependency pinning
Runtime deps are pinned exactly in [`requirements.txt`](../../requirements.txt) (QuantLib wheel
included); dev/CI tools in [`requirements-dev.txt`](../../requirements-dev.txt); the FastAPI service
extras in [`requirements-service.txt`](../../requirements-service.txt).
