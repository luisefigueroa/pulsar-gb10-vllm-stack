# Security

## Reporting

If you find a vulnerability in this repository’s scripts or docs, open a
**private** GitHub security advisory on the repo (or contact the maintainer)
rather than filing a public issue with exploit details.

## Deployment notes

This stack is a **lab / on-prem serving control plane**, not a hardened
multi-tenant SaaS:

- Launchers bind the OpenAI-compatible API to `0.0.0.0` by default for
  cluster convenience. **Do not expose port 8000 (or the worker RDMA/SSH
  plane) to the public internet** without an authenticating reverse proxy,
  network policy, and/or vLLM `--api-key`.
- **Optional first-class key:** set `VLLM_API_KEY` (or `API_KEY`) in `.env`.
  `serve.sh` and the multi-node **head** then pass `--api-key` automatically.
  Unset = open lab default (no auth). Built-in HTTP probes and validation
  clients use the same key as `Authorization: Bearer <key>`. Prefer the
  environment variable to a command-line secret; dry-run launch output
  redacts API and Hugging Face credentials.
- Cluster scripts expect **key-based SSH** from head → worker. Keep those
  keys offline from the git tree; use a local `.env` for `HEAD_IP` /
  `WORKER_IP` and related fabric settings (see `.env.example`).
  Worker calls use BatchMode, bounded connection/liveness settings, and an
  SSH option terminator before the validated host value.
- Never commit `.env`, tokens, or host-specific overlays. `.gitignore`
  already excludes `.env` and common agent instruction files.

Model weights and HF tokens are operator-supplied secrets; this repo does
not ship them.
