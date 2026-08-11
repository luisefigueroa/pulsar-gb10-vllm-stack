# Model-library evidence index

This directory contains sanitized or pending-publication evidence for the
experimental federated model-library path. Evidence records measured history;
it does not override accepted architecture in
[MODEL_LIBRARY_DESIGN.md](../../docs/MODEL_LIBRARY_DESIGN.md) or
[ADR 0001](../../docs/decisions/0001-model-library-home-view-and-validation-identity.md).

The 2026-08-10 promotion assessment remains immutable historical evidence. Its
recommendation to materialize the home rank is **superseded by ADR 0001**. Its
measurements and other failed/pending gates remain valid and are not rewritten.
Catalog schema 2/hot schema 3 expected-seal enforcement, content-addressed
validation-bundle schema 1, and rank-local witness schema 1 landed afterward.
No historical artifact was relabeled as lab-sealed. Maintainer-only
release-candidate tooling now has deterministic schema, exact-commit, tamper,
drift, and output-boundary selftests; candidate generation alone adds no
physical or issuance evidence. Deterministic tests also cover unchanged
metadata, drift fallback, same-size corruption, symlink retargeting, and
rank-local filesystem identity. The 2026-08-11 Qwen artifact adds physical
symlink, witness,
read-only launch, pin/restart, mismatch, and no-follow purge evidence, but it is
explicitly `legacy-unsealed` and does not claim lab-issued identity. Site-local
witness documents contain absolute paths and filesystem identifiers and are
never publishable evidence.

Later on 2026-08-11, the one-node diagnostic `qwen3-1.7b` profile became the
first reviewed lab-issued identity. Its candidate reproduced byte-for-byte,
the exact snapshot and fresh runtime gates passed privacy review, and the
issued seal/bundle then passed physical `library-hot` activation, launch,
zero-byte witness verification, smoke, and cleanup. This does not alter the
legacy scope of the earlier two-node artifact, seal `qwen3-1.7b-2node`, or
promote model-library distribution.

The active-use durable-home removal guard subsequently passed deterministic and
three-node physical checks using only disposable synthetic repositories. Its
artifact closes that lifecycle gate without changing model identity, profile
status, guided defaults, or the Qwen home's retained state.

The filesystem-backed admission policy is implemented and deterministic tests
cover default reserve arithmetic, optional hard caps, durable-home zero charge,
sealed-hot manifest charge, replacement accounting, no-follow inventory,
malformed/untracked bytes, exact all-rank merge, narrow output, and hot-lock
contention. The sanitized non-mutating flagship-sized physical artifact passed
on 2026-08-11; raw `budget --json` output remains site-local because it contains
node and path identity.

| Artifact | Gate / model identity | Status | Privacy review |
|---|---|---|---|
| [`qwen3-1.7b-release-validation-20260811.json`](./qwen3-1.7b-release-validation-20260811.json) | Reviewed release summary for Qwen revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, manifest `775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1`, exact one-node profile, and digest-pinned image | Current first-identity issuance input PASS; not a storage-path promotion | Reviewed; repository-relative evidence only; site identity omitted |
| [`qwen3-1.7b-snapshot-manifest-20260811.json`](./qwen3-1.7b-snapshot-manifest-20260811.json) | Complete `sha256-snapshot-manifest-v1` for the exact issued Qwen revision; 12 files, 4,079,450,110 bytes | Current expected-seal content input PASS | Reviewed; logical snapshot paths and content hashes only |
| [`qwen3-1.7b-release-identity-20260811-runA.json`](../qwen3-1.7b-release-identity-20260811-runA.json) | Fresh exact-profile greedy capture A for the issued one-node Qwen identity | Current release validation PASS; 30-prompt source capture | Reviewed; prompts/results contain no site identity or secrets |
| [`qwen3-1.7b-release-identity-20260811-runB.json`](../qwen3-1.7b-release-identity-20260811-runB.json) | Fresh exact-profile greedy capture B for the issued one-node Qwen identity | Current determinism PASS; 30/30 identical to run A | Reviewed; prompts/results contain no site identity or secrets |
| [`qwen3-1.7b-release-identity-20260811-bench.json`](../qwen3-1.7b-release-identity-20260811-bench.json) | Fresh throughput capture for the issued one-node Qwen identity | Current release performance record; c=1/2/4/8 complete | Reviewed; no site identity or secrets |
| [`qwen3-1.7b-sealed-enforcement-gate-20260811.json`](./qwen3-1.7b-sealed-enforcement-gate-20260811.json) | Post-issuance exact-seal catalog, activation, read-only launch, identity-label, zero-byte witness, smoke, and cleanup proof | Current sealed `library-hot` enforcement PASS; not included in its own issuance bundle and not a promotion | Reviewed; site topology, paths, hosts, nodes, interfaces, and filesystem identity omitted |
| `model-library-hot-budget-admission-gate-20260811.json` | Exact all-rank filesystem admission; DeepSeek revision `7872f01b1d1fe23eabc4c98b48bffcef5a386062`; capacity only, not model identity | Current hot-budget gate PASS; no model bytes changed; not a promotion | Reviewed; site topology, paths, hosts, node/interface identity, and filesystem identity omitted |
| `model-library-home-removal-guard-20260811.json` | Physical all-node active-use removal guard; disposable synthetic HF-layout repositories only | Current lifecycle gate PASS; no production model was deleted; not a promotion | Reviewed; site topology, paths, node/container identity, and filesystem identity omitted |
| `qwen3-1.7b-2node-witness-lifecycle-gate-20260811.json` | Physical durable-home symlink, sealed-hot, witness fallback, exact-snapshot launch, pin/restart, mismatch, and no-follow purge; Qwen revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, observed manifest only | Current lifecycle gate PASS; identity remains `legacy-unsealed`; not a promotion | Reviewed; site topology, paths, filesystem identity, and witness IDs omitted |
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
