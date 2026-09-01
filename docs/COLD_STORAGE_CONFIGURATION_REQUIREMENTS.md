# Cold recovery storage configuration requirements

Accepted live policy is **explicit `PULSAR_COLD_ROOT` only**
([ADR 0015](./decisions/0015-explicit-cold-recovery-root.md)), with inherited
access permissions owned by the operator
([ADR 0016](./decisions/0016-operator-owns-cold-storage-access-control.md)).
This document is the current feature contract for operator configuration,
persistence, guards, and tests.

Deterministic tests make no physical NFS, DGX, archive durability, serving,
qualification, or promotion claim.

## Policy

Precedence:

1. process-level `PULSAR_COLD_ROOT`, including explicit empty;
2. persisted repository `.env` `PULSAR_COLD_ROOT`, including explicit empty;
3. absent means `not-configured`.

There is no live `MODELS_NFS` alias and no implicit `/mnt/Models` cold-recovery
fallback. Existing `cold scan` / `cold show` / `cold adopt` and cold-assisted
resolve require `--cold-root PATH` on that exact invocation. They must not infer
the live recovery root.

The intended later operator choice may be the existing `/mnt/Models`
directory. This product does not edit the operator `.env` by default, create
that directory, run a physical archive job, or touch physical/archive/model
content. Existing non-Pulsar content under `/mnt/Models` remains
byte-for-byte untouched and is not migrated, deleted, rewritten, blessed, or
treated as recovery authority. Once explicitly configured, receipt-backed
jobs own only `$PULSAR_COLD_ROOT/pulsar-control` and
`$PULSAR_COLD_ROOT/pulsar-receipts`. The cold root is not carried in launch
plans and is never mounted into serving containers. Ownership, modes, ACLs,
exports, and administrator access under that root are the operator's policy;
Pulsar accepts the inherited permissions and does not use exact access modes
as recovery admission.

## Live states

| State | Meaning |
|---|---|
| `not-configured` | No persisted preferred assignment and no process override |
| `disabled` | Explicit effective empty preferred value |
| `configured-available` | Explicit preferred path passes required checks |
| `configured-unavailable` | Explicit preferred path is missing, unreadable, unsafe, or store state prevents a healthy view |
| `environment-override` | Process value differs from persisted state; report persisted and effective states/sources separately |

Do not expose `configured-legacy`, `adopt-legacy`, or a default source.

## Commands and menus

```
./pulsar configure cold-storage
./pulsar configure cold-storage show [--json]
./pulsar configure cold-storage plan --path PATH [--json]
./pulsar configure cold-storage set --path PATH --yes [--json]
./pulsar configure cold-storage disable --yes [--json]
./pulsar configure cold-storage archive-jobs [--json]
```

Bare `./pulsar configure cold-storage` opens the interactive workflow.
`./pulsar configure` without a supported topic is usage/exit 2.
`show`, `plan`, and `archive-jobs` are read-only. `set` and `disable` print
the exact preview, require confirmation, recheck immediately, then write.
Direct noninteractive mutation requires `--yes`.

No-argument `./pulsar` first-use: if there is no explicit persisted choice,
offer Configure existing path / Disable / Not now before the main menu.
Not now changes nothing and continues. Cancel/EOF makes no change and
continues to the main menu. Do not prompt on wizard/start/models or
low-level scripts.

Top-level **Configuration** sits beside **Maintenance**:

```
Configuration
  Cold recovery storage
    Show configuration and health
    Set or change storage path
    Disable cold recovery storage
    Inspect archive jobs
    Back
```

All menu actions delegate to the same direct command implementation.

## Persistence

`scripts/model_library_cold_storage.py` owns preferred-key parse/write.
The configuration CLI must not source `.env` as its parser. Parse bytes with
stable no-follow reads. Never print unrelated content or a malformed
sensitive line.

`scripts/lib.sh` captures whether process `PULSAR_COLD_ROOT` is set,
including empty, rejects a symlink or non-regular `.env` before sourcing,
sources an allowed regular `.env` for existing stack behavior, and restores
the process value when it was initially set.

Writer requirements:

- update only one simple `PULSAR_COLD_ROOT=...` assignment or append it;
- explicit disable is `PULSAR_COLD_ROOT=''`;
- preserve every unrelated byte, including non-UTF-8 content;
- reject duplicate, `export`/`declare`, dynamic, computed, trailing-token,
  or ambiguous preferred assignments without auto-repair;
- reject controls/newlines/NUL, leading/trailing whitespace, relative/`~`
  paths, `$()` and backticks;
- preserve ordinary spaces and shell metacharacters as literal path
  characters via safe shell quoting;
- private same-directory temporary file, `O_EXCL`/no-follow, flush + fsync,
  atomic replace, no backup;
- create missing `.env` mode `0600`; preserve an existing private mode;
  refuse symlink/non-regular/group/world-readable target/output;
- original file remains unchanged on every failure;
- serialize concurrent writers with a sibling private lock and re-evaluate
  under the lock.

Test-only dotenv override: `PULSAR_COLD_STORAGE_TEST_DOTENV`, honored only
when `PULSAR_SELFTEST=1`. Production always targets repository `.env`.
Invalid test override usage fails rather than silently redirecting.

## Path plan, health, and strand guard

`plan --path PATH` is read-only and includes normalized lexical absolute
requested path, persisted/effective state and source, exists/directory/
readable/writable observations, final-path symlink refusal, model-library path-safety
and unsafe equality/nesting with controller-local managed roots and known
local occupancy, known controller receipts/jobs/recovery objects affected
by change, action `set-new` / `keep` / `change-blocked`, and the exact
ADR 0014 assertion:

> Pulsar can verify path safety and recovery-set integrity. You assert that
> this storage location meets your recovery and failure-domain policy,
> including access control.

The selected root must already exist. Do not infer NFS or failure-domain
suitability or prescribe mount options. Current writability is reported as
health, not used to accept or reject the operator's configuration choice.
Persist the validated lexical absolute path. A non-persisted
physical-target nest check may refuse unsafe nesting without treating
physical identity as a failure domain.

Changing roots does not migrate; disabling does not delete.

- any job document blocks root change/disable;
- shallow `pulsar-control` or `pulsar-receipts` recovery objects under the
  current explicit root block;
- malformed/unreadable/symlink receipt/job/occupancy/recovery stores make
  health unavailable and mutation blocked;
- a controller receipt alone with no job and no recovery object need not
  block;
- identify affected exact IDs in private JSON and bounded prefixes in
  human output; no hashing of complete archives;
- no force, migration, byte copy, receipt rewrite, schema `cold_root`
  addition, or deletion.

Archive job load/list never mkdir. Missing stores are empty. Exact regular
`<receipt-id>.lock` files created by the owning archive command are recognized
but are not jobs; symlink/non-regular locks and unrelated entries fail without
fallback. Use no-follow stable reads rather than skipping malformed files.

`show` reports persisted/effective state/source, private local path, path
health, shallow counts of receipt replicas/model archives/archive jobs,
counts for pending/running/complete/failed/unavailable, and the authority
boundary. Reads never hash full archives, refresh catalog, create
directories, or mutate jobs.

## Archive jobs and one-job retry

`archive-jobs` lists receipt ID prefix in human output and exact ID in
private JSON, model ID, exact revision, state, and actionable reason
without requiring a live catalog. It is read-only.

Interactive workflow may retry exactly one selected job only when state is
`pending`, `failed`, or `unavailable`, and current controller receipt plus
occupancy resolve. It must show a separate exact preview/confirmation and
then delegate exactly to:

```
scripts/model-library.sh home archive run --receipt RECEIPT_ID --yes
```

Never retry running/complete, bulk retry, loop, delete, prune, scrub,
migrate, or directly publish an archive. Tests replace/intercept that
owning command.

## Human/JSON contracts and exits

Use the shared renderer. Human output remains meaningful at 40 columns and
without color. Confirmation default is no. Exact private paths may appear
in local human/private JSON only; never in tracked docs/results/issues or
Model Serving Release objects.

Plans bind requested value, persisted/effective snapshot, relevant health
bits, sorted affected IDs, and a deterministic plan ID, without clocks.
Mutations recompute under lock immediately before write and compare.

| Exit | Meaning |
|---|---|
| 0 | Successful read or confirmed action; also not-configured/disabled/healthy |
| 1 | Configured unavailable/unsafe or change-blocked |
| 2 | Invalid args, malformed configuration contract, missing `--yes`, or unsupported command |
| interactive cancel/EOF | 0 and no mutation |

## Ownership

- `scripts/model_library_cold_storage.py` owns configuration schemas.
- `scripts/configure-cold-storage.sh` is the thin argv/interactive boundary.
- Existing receipt, occupancy, archive, recovery, prepare, and serving
  schema owners stay authoritative. Configuration is not a new
  receipt/archive authority.
- `scripts/model-library.sh home archive run --receipt ID --yes` is the
  only retry mutation owner.

## Tests

Dedicated `scripts/testlib/test_model_library_cold_storage.py` and
`scripts/selftest-cold-storage.sh`, registered in `scripts/selftest.sh`.
`scripts/selftest-home.sh` stays limited to fixture/index/delegation
integration. Existing home selftests use temp explicit state and never the
operator `.env`.

Coverage includes parser/writer byte preservation, private controller-local
configuration permissions, inherited cold receipt permissions, dotenv shapes,
process override including empty, no `MODELS_NFS` or implicit
`/mnt/Models` live fallback, show/plan/set/disable/archive-jobs human and
closed JSON, stable plan ID, TOCTOU and lock, exits 0/1/2, path guards,
stranded jobs/recovery objects, malformed stores, first-use and menus,
one-job retry interception, 40-column/no-color, and docs/privacy checks.

## Acceptance

- Operators can configure an existing directory, disable cold recovery,
  inspect configuration/health/archive jobs, and retry one eligible job
  from `./pulsar` without hand-editing `.env`.
- Unset is `not-configured`; empty is disable; no implicit default.
- Existing non-Pulsar `/mnt/Models` bytes remain untouched.
- Full `scripts/selftest.sh` passes. No physical claim is created.

## Deferred work

- Recording a root on job documents so a future product can move recovery
  sets instead of blocking.
- Physical NFS, DGX, archive durability, serving, qualification, and
  promotion evidence.
- Inventory or migration of non-Pulsar trees under a configured path.
- Force, delete, or rewrite of stranded recovery objects.
