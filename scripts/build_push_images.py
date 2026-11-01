#!/usr/bin/env python3
"""Build, gate, and push the three Faultline container images (the APP side of deploy).

Builds qlp-backend / qlp-bff / qlp-ui from THIS repo's Dockerfiles for a target platform
(default linux/arm64 — the Graviton estate default), SHA-tags them, runs the golden-PV
governance gate INSIDE the built qlp-backend image as the acceptance (CL-Q5), and pushes to
ECR. The infra repo (aws-genai-iac) owns the downstream deploy — scripts/deploy_qlp_bundle.py
uploads the compose bundle + renders deploy.env, and the EC2 bootstrap pulls these images. The
two repos meet at the ECR registry: infra provisions the genai-dev/qlp-* repos, this pushes to
them. Keeping build+gate here (next to the Dockerfiles + the gate) is the clean boundary.

ECR image refs are:  <registry>/<namespace>/qlp-<component>:<arch>-<sha>
  e.g.  123456789012.dkr.ecr.us-east-1.amazonaws.com/genai-dev/qlp-backend:arm64-abc1234
NOTE the <namespace> (default `genai-dev`): the repos are genai-dev/qlp-*, NOT bare qlp-*.

Run:
    python scripts/build_push_images.py --dry-run                       # print the plan, touch nothing
    python scripts/build_push_images.py --no-push                       # build + gate locally, no push
    AWS_PROFILE=genai-tf python scripts/build_push_images.py --tag arm64-abc1234   # build -> gate -> push
    python scripts/build_push_images.py --registry 123....amazonaws.com --tag arm64-abc1234   # pin the registry

The golden gate needs the data tree mounted (data/); it is ON by default (CL-Q5) — pass
--no-gate to skip it, but a push without the gate is un-accepted. On an x86 host the arm64
build + gate runs under QEMU emulation (slow); that is exactly what confirms the x64-baselined
golden PVs still hold on aarch64. If they drift only there, re-baseline against this image.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# component -> Dockerfile (all built from the repo root so the shared .dockerignore applies).
COMPONENTS = {
    "backend": "infra/backend/Dockerfile",
    "bff": "infra/bff/Dockerfile",
    "ui": "ui/app/Dockerfile",
}
# The in-image acceptance: golden-PV + IPV + model-inventory (exits non-zero on any break).
GATE_CMD = ["python", "-m", "src.pricer.governance_runner"]


def _run(cmd: list[str], dry: bool, **kw: object) -> int:
    print("  $ " + " ".join(cmd))
    if dry:
        return 0
    return subprocess.run(cmd, **kw).returncode  # type: ignore[arg-type]


def _git_sha() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return out.stdout.strip() or "dev"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--platform", default="linux/arm64", help="target platform (default %(default)s)")
    p.add_argument(
        "--namespace",
        default="genai-dev",
        help="ECR repo namespace (default %(default)s) — the repos are <namespace>/qlp-*",
    )
    p.add_argument(
        "--registry",
        default=None,
        help="ECR registry host <acct>.dkr.ecr.<region>.amazonaws.com; else resolved from STS on push.",
    )
    p.add_argument(
        "--tag", default=None, help="image tag (default <arch>-<git-sha>, e.g. arm64-abc1234)."
    )
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--profile", default=None, help="AWS profile for the STS registry-resolve + ECR login.")
    p.add_argument(
        "--component",
        action="append",
        choices=list(COMPONENTS),
        default=[],
        metavar="NAME",
        help="build only this component (repeatable; default: all three).",
    )
    p.add_argument(
        "--no-gate",
        action="store_true",
        help="skip the in-image golden-PV gate (CL-Q5); a push without it is un-accepted.",
    )
    p.add_argument("--no-push", action="store_true", help="build + gate only; do not push to ECR.")
    p.add_argument("--dry-run", action="store_true", help="print the plan; run nothing.")
    args = p.parse_args()

    components = args.component or list(COMPONENTS)
    sha = _git_sha()
    arch = args.platform.rsplit("/", 1)[-1]  # linux/arm64 -> arm64
    tag = args.tag or f"{arch}-{sha}"
    dry = args.dry_run

    # Resolve the registry only when pushing: explicit --registry wins; else STS (or a dry-run placeholder).
    registry = args.registry
    if not args.no_push and not registry:
        if dry:
            registry = f"<acct>.dkr.ecr.{args.region}.amazonaws.com"
        else:
            import boto3

            session = boto3.Session(profile_name=args.profile, region_name=args.region)
            registry = f"{session.client('sts').get_caller_identity()['Account']}.dkr.ecr.{args.region}.amazonaws.com"

    def ref(component: str) -> str:
        repo = f"{args.namespace}/qlp-{component}"
        return f"{registry}/{repo}:{tag}" if registry else f"{repo}:{tag}"

    print(f"platform={args.platform}  tag={tag}  registry={registry or '(local; --no-push)'}")
    print(f"components={components}  gate={'off' if args.no_gate else 'on'}  push={'off' if args.no_push else 'on'}")
    for c in components:
        print(f"  {c:8s} {COMPONENTS[c]:26s} -> {ref(c)}")

    # 1. Build each (single platform, --load so the gate can run it + push works from the local store).
    for c in components:
        cmd = [
            "docker", "buildx", "build", "--platform", args.platform, "--load",
            "-f", str(REPO_ROOT / COMPONENTS[c]), "-t", ref(c),
        ]
        if c == "backend":
            cmd += ["--build-arg", f"GIT_SHA={sha}"]  # provenance; bff/ui take no GIT_SHA
        cmd += [str(REPO_ROOT)]
        print(f"\n== build {c} ==")
        if (rc := _run(cmd, dry)) != 0:
            print(f"ERROR: build {c} failed (rc={rc})", file=sys.stderr)
            return rc

    # 2. Gate: run the golden-PV governance gate INSIDE the built qlp-backend image (CL-Q5).
    if not args.no_gate and "backend" in components:
        data = REPO_ROOT / "data"
        if not data.is_dir():
            print(
                f"ERROR: the golden gate needs the data tree ({data}) mounted; populate it (run the "
                "pipeline) or pass --no-gate to skip. A push without the gate is un-accepted.",
                file=sys.stderr,
            )
            return 2
        print("\n== golden-PV gate (inside qlp-backend) ==")
        gate = [
            "docker", "run", "--rm", "--platform", args.platform,
            "-v", f"{data.as_posix()}:/app/data", ref("backend"), *GATE_CMD,
        ]
        if (rc := _run(gate, dry)) != 0:
            print(
                f"GATE FAILED (rc={rc}) — NOT pushing. If the PVs drifted only on aarch64 vs the x64 "
                "baseline, that is a cross-arch FP delta: re-baseline against THIS image, don't ship a break.",
                file=sys.stderr,
            )
            return rc
        print("  gate PASS")
    elif not args.no_gate:
        print("\n  (golden gate skipped — 'backend' is not in the build set)")

    # 3. Push (ECR login, then docker push each).
    if args.no_push:
        print("\n--no-push: built + gated; nothing pushed.")
        return 0
    if not registry:
        print("ERROR: pushing needs a registry (--registry, or AWS creds for the STS resolve).", file=sys.stderr)
        return 2

    print("\n== ecr login + push ==")
    if dry:
        print(f"  $ aws ecr get-login-password --region {args.region} \\")
        print(f"      | docker login --username AWS --password-stdin {registry}")
    else:
        import base64

        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        token = session.client("ecr").get_authorization_token()["authorizationData"][0]["authorizationToken"]
        user, pwd = base64.b64decode(token).decode().split(":", 1)
        login = subprocess.run(
            ["docker", "login", "--username", user, "--password-stdin", registry], input=pwd, text=True
        )
        if login.returncode:
            print("ERROR: docker login to ECR failed", file=sys.stderr)
            return login.returncode
    for c in components:
        if (rc := _run(["docker", "push", ref(c)], dry)) != 0:
            print(f"ERROR: push {c} failed (rc={rc})", file=sys.stderr)
            return rc

    print(
        f"\nDone. Pushed {len(components)} image(s) at tag {tag}. Next: the infra-side "
        "deploy_qlp_bundle.py --tag " + tag
        if not dry
        else "\nDry run complete; nothing built or pushed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
