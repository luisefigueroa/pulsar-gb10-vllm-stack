# Model-library evidence index

This directory contains publishable evidence for the model-library subsystem.
Model-specific evidence removed by the repository reset is not retained here;
models kept as untested recipe shells must be onboarded again before any new
physical, qualification, or Model Serving Release claim is made.

Current retained evidence:

| File | Scope | Claim boundary |
|---|---|---|
| [`model-library-home-removal-guard-20260811.json`](./model-library-home-removal-guard-20260811.json) | Catalog and artifact service | Synthetic repositories exercise the all-node removal guard. No model was qualified or promoted. |
| [`nemotron-3-nano-source-attested-gate-20260817.json`](./nemotron-3-nano-source-attested-gate-20260817.json) | Catalog and artifact service | Bounded one-rank Hugging Face acquisition, receipt, prepare, cleanup, and reacquisition evidence. It does not prove serving, model qualification, remote-target acquisition, or a Model Serving Release decision. |

## Publication rules

- Use repository-relative links only; never publish user home or workspace paths.
- Omit or redact hostnames, addresses, SSH identity, node IDs, interface names,
  topology IDs, credentials, and filesystem identity.
- State the qualification scope. Catalog and artifact evidence cannot satisfy
  a Model Serving Release validation criterion.
- Publish new model evidence only through a fresh onboarding and review flow.
