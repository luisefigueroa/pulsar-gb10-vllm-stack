# Repository Guidelines

## Project Structure & Module Organization

This repository is an operations and validation stack for serving vLLM on one or two NVIDIA DGX Spark GB10 systems. Entry points are `serve.sh` and `wizard.sh`; higher-level lifecycle commands live in `scripts/`, while `cluster/` contains two-node launch, stop, and preflight tooling. Model profiles are shell-style files under `models/`. Python benchmarks and correctness checks live in `validate/`, with measured artifacts in `results/` and hardware probes in `bench/`. Keep operational explanations in `docs/`; deprecated experimental overlays belong in `patches/`.

## Build, Test, and Development Commands

- `scripts/selftest.sh` runs control-plane tests and Python syntax checks without requiring Docker.
- `scripts/doctor.sh` verifies GPU, Docker, port, cache, and optional worker readiness on GB10 hardware.
- `scripts/list-models.sh --validated` lists profiles approved for use.
- `scripts/up.sh qwen3-1.7b --dry-run` exercises launch checks without starting a server.
- `validate/run-gates.sh <served-name> --tag <label>` runs determinism captures, throughput benchmarks, and optional baseline/needle gates against an already-running server.
- `docker build -t vllm-gb10:v0.26.0 .` builds the optional metadata overlay; see `docs/BUILD.md` before changing image pins.

## Coding Style & Naming Conventions

Write Bash for orchestration (`#!/usr/bin/env bash`, `set -euo pipefail`) and Python 3 for validation utilities. Use two-space indentation in shell blocks and four spaces in Python. Quote shell expansions, prefer arrays for command construction, and add narrowly scoped ShellCheck suppressions with a reason. Use `lowercase-hyphenated.sh` for scripts, `snake_case.py` for Python, and descriptive model IDs such as `nemotron-3-nano-30b-nvfp4`. Preserve existing config key conventions (`UPPER_SNAKE_CASE`).

## Command-Line Experience

Treat human-readable command-line output as a primary product requirement. Optimize interactive and human-facing output for fast scanning with clear information hierarchy, semantic line breaks, hanging indentation, consistent labels, and readable behavior at narrow terminal widths. Avoid dense key/value streams, uncontrolled wrapping, and meaning conveyed by color alone. Keep machine-readable output, such as JSON, separate and stable. For every CLI-facing change, review the rendered human output explicitly and test representative narrow terminal widths.

## Testing Guidelines

Run `scripts/selftest.sh` for every script or config change. Changes affecting serving behavior must also follow `docs/REVALIDATE.md`; record reproducible outputs under `results/` and update `docs/VALIDATION.md`. There is no percentage coverage target: promotion depends on correctness, determinism, benchmark, long-context, and soak evidence appropriate to the change.

## Commit & Pull Request Guidelines

History favors concise imperative subjects, usually Conventional Commit style: `fix(memory): ...`, `feat(serve): ...`, or `docs(patches): ...`. Keep commits focused. Pull requests should explain affected models and hardware paths, link relevant issues, list commands run, and include result artifact paths. Highlight image/config changes and any behavior not validated on physical GB10 hardware.

## Security & Configuration

Never commit `.env`, API/Hugging Face tokens, SSH keys, or model weights. The API binds to `0.0.0.0:8000`; keep it on a trusted lab network or configure `VLLM_API_KEY` and an authenticating proxy. Report vulnerabilities privately as described in `SECURITY.md`.
