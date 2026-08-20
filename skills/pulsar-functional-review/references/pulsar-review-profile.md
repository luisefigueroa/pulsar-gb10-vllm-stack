# Pulsar Review Profile

This file is the repository-specific seed map for `pulsar-gb10-vllm-stack`. Discover current equivalents when paths have moved.

## Canonical operator workflows

### First-run and onboarding

Seed documentation and entry points:

- `README.md`
- `docs/PREREQUISITES.md`
- `wizard.sh`

Trace from a fresh supported checkout through prerequisite discovery, configuration generation, credentials or permissions, model selection, and the next documented action. Check whether the wizard covers every path the docs promise and whether exits/errors explain the next corrective step.

### Model acquisition and library lifecycle

Seed files:

- `scripts/model-library.sh`
- `scripts/model_library.py`
- `docs/MODELS.md`
- `docs/MODEL_LIBRARY_DESIGN.md`
- `docs/MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md`

Trace: requested model or profile -> authentication/revision selection -> storage target -> download/import -> completeness/integrity state -> catalog visibility -> path consumed by serving -> retry/update/removal behavior.

### Serving

Seed files:

- `serve.sh`
- `docker-compose.yml`
- `docs/RECIPES.md`
- model/profile definitions discovered in the repository

Trace every supported recipe from documentation through argument parsing, profile resolution, environment generation, Compose interpolation, mounts, container command, service name, health/readiness signal, logs, stop/restart, and model switching.

### Multi-node lifecycle

Seed files:

- `cluster/*.sh`
- `docs/MULTINODE.md`
- current operations and troubleshooting documentation

Trace: node discovery/identity -> prerequisite and topology preflight -> distribution or shared-storage preparation -> start -> readiness/health -> degraded node or node loss -> retry/rejoin/restart -> teardown and cleanup. Verify that documented topology, rank ordering, transport, ports, mounts, and node-state assumptions match the implementation.

### Diagnostics and recovery

Seed files:

- `scripts/doctor.sh`
- `scripts/quick-status.sh`
- `docs/TROUBLESHOOTING.md`
- diagnostics or log helpers discovered from public entry points

For each major workflow failure path, determine whether the operator receives a specific diagnosis, next action, and route back to a known-good state. Check stale locks, partial downloads, failed containers, missing credentials, bad profiles, unavailable nodes, mismatched state, and teardown/retry paths when the repository claims to support them.

### Benchmarking and revalidation

Seed paths:

- `bench/`
- `validate/`
- `docs/REVALIDATE.md`
- validation claims in README, recipes, model docs, and decisions

Trace: documented command -> shipped runner -> referenced model/profile/image/version -> input dataset or prompt set -> configuration -> output artifact -> comparison or acceptance rule -> interpretation. Determine whether a user can reproduce what the repository calls validated without private files, unstated versions, unavailable scripts, or undocumented manual transformations.

## Feature contract and selftests

Compare current supported claims in:

- `README.md`
- `docs/MODEL_LIBRARY_DESIGN.md`
- `docs/MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md`
- `docs/decisions/`
- current operations, recipes, and troubleshooting docs

against actual implementation and discoverability.

Inspect:

- `scripts/selftest-*.sh`
- other test or validation scripts that claim functional coverage

Map each selftest to the user-visible behavior it meaningfully asserts. Identify workflows with no substantive selftest, but do not equate test absence alone with a broken user workflow.

## Cross-cutting contracts

Inspect these across all packets:

- configuration and environment-variable precedence
- profile schema and defaults
- generated state ownership and lifecycle
- filesystem paths, mounts, permissions, and credentials
- local cache vs shared/model-library storage behavior
- version/image/model revision pinning
- exit codes, success signals, and health checks
- idempotency, retries, cleanup, and stale state
- documentation links and command discoverability
- optional/experimental capability labeling
