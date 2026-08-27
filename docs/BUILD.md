# Build and image provenance

## Policy

Use a published arm64 vLLM image pinned by digest in each model profile. A
profile's `IMAGE` value is part of its Model Serving Release identity; changing
the digest creates a new subject and requires revalidation.

Do not infer an image pin from an old model result. The current tracked Model
Serving Release registry is empty, and retained untested recipe shells carry no
prior image qualification.

## Current image sources

- The optional repository `Dockerfile` overlays
  `vllm/vllm-openai:v0.26.0` at the digest declared by `VLLM_BASE`.
- Profiles that set `IMAGE` use that exact digest instead of the overlay
  default. Inspect `models/<profile>.conf` before staging.
- Tags are mutable and are not qualification identity. Record the manifest
  digest used on every rank.

Build the optional metadata overlay with:

```bash
docker build -t vllm-gb10:v0.26.0 .
```

The overlay keeps the upstream serving environment and adds repository
metadata plus a build-time architecture check. It does not add validation
dependencies or qualify a model.

Stage the exact candidate image to every rank required by its profile:

```bash
scripts/sync-image.sh <profile> --pull --yes
```

Then run the full workflow in [`REVALIDATE.md`](./REVALIDATE.md). A successful
pull or overlay build proves only that the image is present; it does not prove
runtime compatibility, model behavior, or a reviewed Model Serving Release.

## When a source build is justified

Use a source build only when the exact candidate needs an unreleased
architecture or kernel change that a published arm64 image does not contain.
If that occurs:

1. Pin the exact upstream commit and toolchain.
2. Record a stranger-reproducible build recipe and immutable output digest.
3. Treat the resulting image as a new Model Serving Release input.
4. Re-run serving integration and every frozen model-qualification criterion.
5. Do not publish model-specific claims until the evidence and registry objects
   are reviewed and merged.

CUDA architecture selection and dependency versions are build inputs, not
proof of physical GB10 behavior. Verify the resulting image on the exact target
geometry.
