# Model-library evidence index

This directory contains sanitized or pending-publication evidence for the
experimental federated model-library path. Evidence records measured history;
it does not override accepted architecture in
[MODEL_LIBRARY_DESIGN.md](../../docs/MODEL_LIBRARY_DESIGN.md) or
[ADR 0001](../../docs/decisions/0001-model-library-home-view-and-validation-identity.md).

The 2026-08-10 promotion assessment remains immutable historical evidence. Its
recommendation to materialize the home rank is **superseded by ADR 0001**. Its
measurements and other failed/pending gates remain valid and are not rewritten.

| Artifact | Gate / model identity | Status | Privacy review |
|---|---|---|---|
| `model-library-promotion-assessment-20260810.json` | Consolidated DeepSeek/Qwen promotion assessment; schema-2 local content digest, not a lab-issued expected seal | Historical assessment; materialization recommendation superseded | Embedded redaction declaration; topology/site fields omitted |
| `topology-ssh-trust-gate-20260810.json` | Topology-bound SSH alias/key/endpoint gate and Qwen sealed activation | Current gate evidence | Embedded redaction declaration |
| `deepseek-v4-flash-ssh-roce8-vs-control8-counterbalanced-20260810.json` | DeepSeek full-model `ssh-control` versus `ssh-roce` performance/traffic proof | Current performance evidence | Embedded redaction declaration |
| `deepseek-v4-flash-parallel-rsync-roce-8v16-alternating-20260810.json` | DeepSeek 8-versus-16-stream alternating trials | Current stream-selection evidence | Embedded redaction declaration |
| `deepseek-v4-flash-parallel-rsync-roce-16stream-20260810.json` | Initial variable 16-stream exploration | Historical exploratory evidence | Embedded redaction declaration |
| `deepseek-v4-flash-dsv4-*.json` and `dsv4-bench-*.log` | Earlier copy versus one-shot NFS/RDMA activation work | Historical/superseded performance evidence | **Pending sanitation:** stable topology IDs and/or absolute workspace paths remain |
| `codex-fabric-review-20260809.md` | Static review that motivated parallel copy and no home duplication | Historical design evidence; home-view conclusion retained | **Pending sanitation:** absolute local repository links remain |

## Publication rules

- Use repository-relative links only; never publish user home/workspace paths.
- Omit or redact hostnames, IPs, SSH aliases/keys, node IDs, interface names,
  topology IDs, credentials, and other stable site identifiers.
- State the exact model revision/manifest or explicitly say when an artifact
  predates lab-issued expected-seal binding.
- Mark evidence `current`, `historical`, `superseded`, `failed`, or
  `partial`; never delete or rewrite a failure into a pass.
- A superseding ADR or ledger changes the current decision, not the historical
  measurements.
