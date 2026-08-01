# DSpark multi-node draft-path optimizations, overlaid on the PR-41834 build.
# Pure-Python port of the GB10 fork's cross-node collective eliminations
# (see README.md in this directory). Rebuild takes seconds; the base image
# provides all compiled kernels.
FROM vllm-gb10:pr41834-d64074e6f

LABEL gb10.patch="dspark-opt-v1" \
      gb10.patch.source="fork vllm-dspark-runtime stage-c (LOCAL_ARGMAX/REPLICATE_MARKOV_W1), semantically ported onto PR#41834 d64074e6f"

COPY dspark.py /usr/local/lib/python3.12/dist-packages/vllm/models/deepseek_v4/nvidia/dspark.py
COPY speculator.py /usr/local/lib/python3.12/dist-packages/vllm/v1/worker/gpu/spec_decode/dspark/speculator.py
COPY envs.py /usr/local/lib/python3.12/dist-packages/vllm/envs.py
