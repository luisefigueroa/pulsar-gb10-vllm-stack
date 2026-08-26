# Pulsar consistency audit checklist

## Contents

1. Repository inventory
2. Workflow traces
3. Cross-cutting contracts
4. Model-library invariants
5. Tests and evidence
6. Terminology
7. Permitted checks and stop conditions

## Repository inventory

Inspect at minimum, where present:

- `AGENTS.md`, `README.md`, `PREREQUISITES.md`, and `MODELS.md`;
- `pulsar`, `wizard.sh`, `serve.sh`, and operator help/output;
- `models/*.conf` and `models/model-serving-releases/` (not archived
  `docs/archive/schema-1-expected-seal/`);
- `scripts/`, `scripts/testlib/`, `cluster/`, `validate/`, and `bench/`;
- `docs/`, especially accepted ADRs, current specifications, operations,
  revalidation, validation, and model/fabric design documents;
- `results/`, its evidence indexes, and privacy/supersession metadata;
- `patches/`, the Dockerfile, and image/version metadata; and
- selftest entrypoints and focused contract suites.

Determine which documents identify their role clearly: normative architecture,
descriptive current system, operator runbook, validation ledger, or historical
evidence. Flag unclear roles that create real interpretive ambiguity.

## Workflow traces

Trace these workflows through policy, code, UI, tests, and evidence:

1. **Installation and prerequisites:** actual binaries, privileges, paths,
   environment variables, and failure behavior versus documented requirements.
2. **Topology and Doctor:** creation/refresh, cached and stale state, single- and
   two-node behavior, JSON/human parity, severity, and remediation.
3. **Model discovery and selection:** tested/experimental filters, exact model
   and revision, receipts/file lists, interactive identity, and similar/truncated
   labels.
4. **Library serving:** topology confirm, `home add`, catalog refresh, prepare,
   launch, health, status, stop, pin/purge, and cleanup. There is no replicated
   or live-NFS serving path.
5. **Validation and release:** exact identity and runtime inputs, serving
   integration, model qualification, release/promotion, and invalidation rules.
6. **Failure and recovery:** partial preparation/start, unreachable ranks, stale
   topology, drift/mismatch, missing home, budget exhaustion, active references,
   interrupted cleanup, and restart behavior.

## Cross-cutting contracts

For public commands and subsystem APIs, compare:

- usage/help, accepted arguments, defaults, and actual dispatch;
- exit codes and documented success/failure meaning;
- human-readable output, narrow-terminal rendering, and stable JSON schemas;
- confirmation and ownership checks for destructive actions;
- fail-without-fallback behavior and whether remediation commands are valid;
- interactive wrappers and direct CLI policy equivalence;
- control, inference, and weight-transfer plane separation;
- explicit experimental gates and absence of silent fallback; and
- privacy of JSON, logs, docs, and publishable evidence.

Sanitized contracts must not leak prohibited absolute paths, hostnames, IPs,
node/topology IDs, filesystem identities, witness IDs, or repair IDs.

## Model-library invariants

Verify consistency across doctrine, code, UI, tests, operations, and evidence:

- the model library is the only weight-distribution mechanism (ADR 0006); no
  mode-selection flags exist, and `--weight-source`/`--weight-mode` fail
  without fallback with remediation;
- a confirmed topology manifest (one-node is valid) is a serving prerequisite;
- operator language says “prepare model for serving”; public `activate` is
  removed (ADR 0008) and fails without fallback with `prepare`; `activate` remains an
  internal-schema term;
- no silent transport, storage, geometry, or cache fallback occurs;
- live NFS/RDMA serving (`live-remote-readonly`) is rejected (ADR 0005) and is
  not offered as a serving alternative;
- one durable home exists per exact revision by default;
- the home rank uses its durable-home view, never a second hot copy;
- only non-home ranks receive working copies (`runtime_source=working-copy`);
- hot copies are budgeted and lifecycle-managed;
- pin retains hot copies but does not promise durable-home-loss resilience;
- durable-home deletion is separate and confirmation-gated;
- hot cleanup never follows the durable-home symlink;
- exact revision, receipt, and file list remain visible;
- observed local content cannot issue or replace trusted identity;
- full verification occurs at trust boundaries;
- metadata witnesses accelerate only previously verified unchanged launches;
- witness drift causes full verification against the receipt or fails without fallback;
- catalog refresh is explicit, with cached age/topology staleness visible;
- reviewed-profile preparation uses topology-bound `ssh-roce`, eight streams,
  and no automatic fallback where ADR 0003 applies; and
- catalog/artifact, serving-integration, model-qualification, and
  release/promotion claims stay distinct.

## Tests and evidence

For each important behavior, record its strongest support:

- source only;
- focused automated contract test;
- general selftest;
- physical GB10 evidence;
- current validation evidence;
- historical/superseded evidence only; or
- none.

Check for important missing branches, stale fixtures, implementation-coupled
assertions, hostname-coded product roles, duplicated mocks, oversized scenarios,
unsupported physical claims, missing privacy audits, and evidence that cannot
reconstruct exact model/runtime inputs.

Do not treat selftests as physical proof. When reviewing a claim, compare model
revision, receipt, file list, image digest, normalized profile/configuration,
geometry, runtime flags, evidence date, and supersession state.

## Terminology

Build a consistency table covering at least:

`home`, `owner`, `primary`, `rank 0`, `prepare`, `preparation`, `activation`,
`durable-home`, `working-copy`, `live-mount`,
`replicated`, `catalog`, `local-files` (leftover containers may still say
`library-hot`), `fabric`, `ssh-control`, `ssh-roce`, `nfs-rdma`, `durable`,
`ephemeral`, `pinned`, receipt, occupancy, `witness`, file list,
ADR 0004 evidence bundle, `serving integration`, `model qualification`, and
`promotion`. Flag live use of archive-only nouns: expected-seal,
`EXPECTED_MODEL_SEAL`, `identity_status=match`, `validation-bundle verify`,
`model-release.sh`.

Flag conflicting definitions, undefined or overloaded terms, obsolete
operator-visible language, and ambiguity among control, inference, and transfer
planes. Do not elevate harmless wording variation into a defect.

## Permitted checks and stop conditions

Use read-only or non-mutating checks such as `rg`, `rg --files`, syntax checks,
`--help`, dry runs, focused selftests, `scripts/selftest.sh`, docs/link checks,
`git diff --check`, JSON parsing, and privacy scans.

Do not refresh/rewrite site-local state, prepare/copy/purge/repair/delete model
data, operate containers, alter topology, run hardware experiments, fabricate
seals/evidence, edit historical artifacts, create issues, or publish changes.
Record the needed follow-up when a check crosses one of these boundaries.
