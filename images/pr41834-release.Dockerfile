# Release wrapper for the validated local PR #41834 image.
#
# This does not rebuild or replace the qualified vLLM/CUDA layers; it applies
# release metadata plus a narrowly pinned OS security refresh. BASE_IMAGE must
# resolve to the image that passed the repository's DeepSeek-V4 validation
# gates.
ARG BASE_IMAGE=vllm-gb10:pr41834-d64074e6f
FROM ${BASE_IMAGE}

ARG BUILD_DATE
ARG RELEASE_IMAGE=ghcr.io/luisefigueroa/pulsar-gb10-vllm-stack:pr41834-d64074e6f
ARG SOURCE_REVISION=d64074e6f07250f6cd072861aa3c389a929befb9

# Security refresh applied after qualification. These packages are not part of
# the vLLM/CUDA execution path, but the base image versions are affected by
# CVE-2026-45447. Exact versions keep this release wrapper reproducible.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
       libssl3=3.0.2-0ubuntu1.26 \
       openssl=3.0.2-0ubuntu1.26 \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="pulsar-gb10-vllm-stack PR-41834 vLLM" \
      org.opencontainers.image.description="Validated arm64 vLLM OpenAI server for DeepSeek-V4-Flash and DSpark on NVIDIA GB10" \
      org.opencontainers.image.source="https://github.com/luisefigueroa/pulsar-gb10-vllm-stack" \
      org.opencontainers.image.url="https://github.com/luisefigueroa/pulsar-gb10-vllm-stack" \
      org.opencontainers.image.documentation="https://github.com/luisefigueroa/pulsar-gb10-vllm-stack/blob/main/docs/BUILD.md" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="pr41834-d64074e6f" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0 AND LicenseRef-NVIDIA-Deep-Learning-Container" \
      ai.vllm.build.commit="${SOURCE_REVISION}" \
      ai.vllm.build.pipeline="pulsar-gb10-qualified-local" \
      ai.vllm.build.url="https://github.com/vllm-project/vllm/pull/41834" \
      ai.vllm.image.tag="${RELEASE_IMAGE}" \
      io.pulsar.gb10.validation="deepseek-v4-flash-dspark-qualified-2026-08-01" \
      io.pulsar.gb10.security-refresh="CVE-2026-45447" \
      io.pulsar.gb10.base-image-id="sha256:8828d9edc8e40f993c2bdccfb00cbab875eae1e6bafbf0af2f655c29ea4546ed"

ENV VLLM_BUILD_COMMIT=${SOURCE_REVISION} \
    VLLM_BUILD_PIPELINE=pulsar-gb10-qualified-local \
    VLLM_BUILD_URL=https://github.com/vllm-project/vllm/pull/41834 \
    VLLM_IMAGE_TAG=${RELEASE_IMAGE}
