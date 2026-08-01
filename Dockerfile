# vLLM serving image for NVIDIA DGX Spark (GB10, sm_121, aarch64).
#
# DECISION: overlay on the official multi-arch image, NOT a source build.
# vLLM v0.26.0 (2026-07-25 arm64 push) ships CUDA 13.0.x wheels whose kernels
# are built with the 12.0f *family* target, which covers sm_121 natively —
# verified on this machine by kernel-inventory + smoke inference (docs/BUILD.md).
# A source build (~3-5 h on the 20-core Grace CPU) would add nothing except the
# ability to chase unmerged PRs; if that changes (e.g. sparse-MLA-on-sm_121
# lands), build from source at that commit and update this file.
#
# The DIGEST is the pin. Tags are mutable; this digest is the exact multi-arch
# manifest verified by the validation suite in docs/VALIDATION.md.
ARG VLLM_BASE=vllm/vllm-openai:v0.26.0@sha256:ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52

FROM ${VLLM_BASE}

LABEL org.opencontainers.image.title="pulsar-gb10-vllm-stack" \
      org.opencontainers.image.description="Validated vLLM serving stack for one or two NVIDIA DGX Spark GB10 systems" \
      org.opencontainers.image.source="https://github.com/luisefigueroa/pulsar-gb10-vllm-stack" \
      gb10.vllm.upstream="v0.26.0" \
      gb10.validated="see docs/VALIDATION.md"

# Fail at build time, not at 2am, if the base image is the wrong arch.
RUN [ "$(uname -m)" = "aarch64" ] || { echo "wrong arch: $(uname -m)"; exit 1; }

# curl for the /health healthcheck; python deps for the validation harness are
# NOT installed here — the serving image stays exactly upstream + metadata.
RUN command -v curl >/dev/null || (apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*)
