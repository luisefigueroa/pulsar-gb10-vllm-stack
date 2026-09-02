# Model library preparation and serving

> **Authority: canonical model-library architecture.**
>
> Current procedures are in [OPERATIONS.md](./OPERATIONS.md). The live catalog
> is in [MODELS.md](./MODELS.md) and `models/*.conf`. Evidence is in
> [VALIDATION.md](./VALIDATION.md) and `results/`.

The model library is the only weight-distribution mechanism
([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)).
Every serving rank uses local files. Live NFS/RDMA under vLLM is rejected by
[ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md).

The tracked Model Serving Release registry is empty. No current profile sets
`MODEL_SERVING_RELEASE_ID`. Retained Qwen3.8 and Qwen3 1.7B recipe shells are
unbound and untested; they carry no retained model-specific evidence.

## 1. Product requirements

| Requirement | Success condition |
|---|---|
| Storage efficiency | One complete durable home per exact revision by default; non-home copies are working sets |
| Startup time | Reuse verified local files and avoid unnecessary transfer while preserving exact identity |
| Reliability | Fail without fallback on identity, topology, capacity, transport, lifecycle, or verification failure |

Stability has priority over accuracy, throughput, and latency. A faster path
that weakens identity, rollback, cleanup, or recovery is not acceptable.

## 2. Independent state dimensions

Do not collapse these facts into one status:

| Dimension | Examples |
|---|---|
| Artifact identity | Model ID, exact commit, complete file list, SHA-256 |
| Durable placement | Which confirmed rank currently holds the home |
| Runtime source | Home symlink or non-home working copy |
| Preparation state | Ready, missing, incomplete, drifted |
| Retention | Unpinned, pinned, explicit purge |
| Recovery | Receipt replica and model archive state |
| Profile recommendation | Legacy `STATUS` value |
| Reviewed status | ADR 0004 decision for one exact Model Serving Release |

Presence is not identity. A complete-looking directory cannot authorize itself.
A healthy endpoint is not model qualification. A reviewed status is not serving
permission.

## 3. Durable homes and occupancy

A **home** is the one complete durable on-disk copy of an exact revision. The
live authority for a Hugging Face home is:

1. an immutable **receipt** recording the exact public source, commit, complete
   file list, sizes, and SHA-256 values; and
2. a private **occupancy** attachment naming the exact live directory identity.

The receipt is stored in the controller's private
`.model-library/download-receipts/` state, not inside the model repository.
Occupancy binds node, path, device, inode, and ctime observations to that
receipt. Replacing a directory at the same path does not preserve occupancy.

`home add --revision` is the current acquisition path for an absent Hugging
Face repository:

1. resolve a public selector to an exact commit and complete Git/LFS inventory;
2. show a read-only plan;
3. require separate confirmation for that exact plan;
4. use authentication local to the selected rank;
5. download into private same-filesystem staging;
6. verify the upstream file set and every SHA-256;
7. repeat the all-rank absence check;
8. write the immutable receipt;
9. publish with atomic no-replace rename; and
10. attach occupancy to the published directory identity.

Acquisition does not refresh the catalog, prepare a runtime view, launch,
change profile status, or issue a Model Serving Release decision.

`home verify` performs a complete offline rehash against the receipt while
occupancy still names that exact directory. An unknown or replaced tree without
that proof fails without fallback.

## 4. Portable occupancy

Receipt provenance does not permanently assign a model to the download rank.
`home relocate --node` may move occupancy after a complete live rehash.

- A one-rank profile may place its home on any confirmed rank.
- A multi-rank profile may use only one of that profile's exact serving ranks.
- A raw `model_id@revision` query requires `--profile`; shared bytes never
  guess a recipe or geometry.
- Geometry validation occurs before catalog access, destination inspection, or
  mutation.
- Relocation does not refresh the catalog, prepare working copies, or launch.

See [ADR 0011](./decisions/0011-portable-occupancy-and-cold-archive.md).

## 5. Catalog

`catalog refresh` is read-only with respect to model bytes. Rank scanners
discover complete exact-revision repositories and record directory identity.
The Python catalog builder owns one transaction:

1. load and validate all scan results;
2. load receipts and occupancy strictly;
3. classify occupied homes and unbound complete trees;
4. recompute primary policy; and
5. atomically write the mode-`0600` catalog and emit the same final object.

Corrupt receipt or occupancy state preserves the previous catalog. Shell code
does not patch catalog JSON after the builder returns.

Multiple complete trees for the same identity are not alternate homes.
Duplicate cleanup and primary selection are explicit operator actions. Catalog
refresh never deletes bytes or silently changes placement.

Every complete tree receives an explicit class. Only a tree whose receipt and
occupancy attachment match the live directory counts as a home. An externally
populated tree, a cold-adopted tree, or an old cached tree with no such
authority is `unbound-complete`; there is no complete-tree fallback.

## 6. Preparation and runtime views

**prepare** creates verified local files for an exact profile. It does not
start vLLM.

On the home rank, preparation creates and validates an exact symlink to the
durable snapshot. It does not create a second home-rank copy. On every non-home
serving rank, preparation creates a working copy in the managed hot root.

For multi-rank profiles, the fixed transfer policy is:

~~~bash
scripts/model-library.sh prepare <multi-rank-profile> \
  --backend copy --transport ssh-roce --copy-streams 8 --yes
~~~

Preparation requires confirmed topology, enrolled SSH identity, the exact RoCE
endpoint, sufficient capacity, and the receipt-backed home. It fails without
fallback; it does not switch to control-network copy, another rank set, another
storage source, or another stream policy. One-rank preparation performs no
bulk inter-rank transfer.

Before capacity calculation or copying, multi-rank preparation verifies that
the occupied home is one of the profile's exact serving ranks. If not, the
operator relocates occupancy into those ranks, refreshes the catalog, and
retries the same fixed preparation policy.

## 7. Verification and witnesses

Full SHA-256 verification occurs during acquisition, `home verify`,
relocation, each non-home copy, serve-time witness drift, and cold recovery
verification.

After full preparation verification, each rank receives a private **witness**:
saved path and file metadata that permits a fast unchanged-tree check at start.
It binds the canonical target, exact revision, file set, and per-file device,
inode, size, mtime, and ctime.

A witness is a cache of completed verification, not a trust root. Drift causes
a visible full rehash against the receipt. Success may refresh the witness
atomically; mismatch fails without fallback and does not bless the new bytes.
Launch passes the exact snapshot path and mounts model repositories read-only.

## 8. Pins, purge, and stop

- Ordinary stop retains unpinned prepared views
  ([ADR 0007](./decisions/0007-ordinary-stop-retains-unpinned-hot-views.md)).
- **pin** protects a working copy from unforced purge.
- **purge** is explicit capacity recovery.
- Pinning does not protect or duplicate the durable home.
- `down.sh --all` does not silently purge working copies.

Removal is ownership-safe and no-follow. Pulsar stops or deletes only resources
whose stack ownership it can prove. An unobservable required rank blocks
multi-rank cleanup and replacement.

Home removal is a separate destructive workflow. It rechecks repository shape,
directory identity, working copies, containers, readers, and last-home recovery
requirements before detaching occupancy.

## 9. Cold recovery set

After occupancy attachment, a nonblocking job publishes two separate objects:

1. a byte-identical receipt replica in the separate
   `pulsar-control/download-receipts/` namespace; and
2. the receipt-indexed model archive under `pulsar-receipts/`.

The receipt replica is control state and is never inserted into the model
archive. Archived bytes and `presence.json` cannot create or authorize a
receipt.

Unless the operator explicitly accepts unarchived loss, last occupancy removal
requires the canonical receipt, its control-state replica, and a complete rehash of
the separate model archive.

A missing controller receipt is recovered only through explicit,
confirmation-gated `home receipt recover`. Restore requires receipt identity,
receipt-sized admission, all-rank absence, private same-filesystem staging, a
complete rehash, another absence check, and atomic no-replace publication. New
occupancy is derived from the new live directory; catalog refresh is separate.

Live recovery configuration is explicit `PULSAR_COLD_ROOT` only
([ADR 0015](./decisions/0015-explicit-cold-recovery-root.md)). Process,
then persisted repository `.env`, then `not-configured`. Empty disables.
There is no live `MODELS_NFS` alias and no implicit `/mnt/Models`
fallback. Operators set, disable, inspect, and retry one eligible archive
job through `./pulsar configure cold-storage`. The selected directory must
already exist. Pulsar never creates, mounts, or administers it. Existing
non-Pulsar content stays untouched. Receipt-backed jobs own only
`pulsar-control` and `pulsar-receipts` under that root. Root changes do
not migrate; disable does not delete. The cold root is not part of the launch
plan and is never mounted into a serving container.

The operator owns whether the configured cold root is a suitable independent
failure domain and owns its access-control policy. Pulsar accepts inherited
ownership, modes, and ACLs under the cold root. It checks operational access,
path safety, canonical receipt identity and equality, and recovery-set content;
it does not compare devices, mounts, filesystems, exports, or topology to prove
storage independence, and it does not enforce Unix access modes there
([ADR 0014](./decisions/0014-operator-owns-cold-storage-failure-domain.md),
[ADR 0016](./decisions/0016-operator-owns-cold-storage-access-control.md)).

## 10. Model Serving Release boundary

A **Model Serving Release** freezes exact model identity, serving recipe,
runtime/image identity, and supported hardware geometry. Changing any part
creates a new release. Transfer, placement, receipt, occupancy, evidence, and
review metadata do not enter the release ID.

A frozen **Validation Contract** declares criteria and thresholds. Run records
and evidence bundles preserve observations. A reviewed decision uses one of:
`Untested`, `Testing incomplete`, `Tested—criteria not met`,
`Tested—inconclusive`, `Validated`, or `Superseded`.

The tracked registry keeps descriptor, contract, run record, evidence bundle,
and decision objects separate. It is currently empty. A local draft or staged
proposal is not trusted; repository review and merge establish registry state.

Profiles may set `MODEL_SERVING_RELEASE_ID` only when the complete reviewed
object graph is published in the same change. Catalog display consumes the
verified decision for that binding. Start does not use it as permission.

## 11. Qualification scopes

| Scope | Question |
|---|---|
| Catalog and artifact service | Are exact bytes identified, placed, transferred, retained, recovered, and cleaned up correctly? |
| Serving integration | Did the exact image and launcher load the intended verified local files and stop cleanly? |
| Model qualification | Did the exact subject meet stability, accuracy, throughput, latency, context, soak, and strict same-boot criteria? |
| Release and promotion | Did provenance/security and physical geometry pass, and do all required scopes combine for one reviewed decision? |

A failure does not erase evidence from another scope without a demonstrated
causal connection. It blocks every combined claim that requires the failed
scope.

Current retained catalog/artifact evidence is indexed in
[`results/model-library/README.md`](../results/model-library/README.md). The
Nemotron Nano Gate 14 is a bounded one-rank acquisition lifecycle result; it
does not prove remote-target acquisition, asymmetric credentials, serving, or
model qualification.

## 12. Promotion and revalidation

An untested recipe shell is not a continuation of removed evidence.
Re-onboarding begins with new source resolution, receipt-backed acquisition,
frozen criteria, physical runs, privacy review, and a new reviewed proposal.

Before publication:

1. verify exact identity and every runtime view;
2. pass required serving-integration and model-qualification gates;
3. account for every applicable observation;
4. complete provenance/security and physical-geometry review;
5. run the deterministic full selftest;
6. publish only privacy-cleared evidence; and
7. merge the complete registry graph and optional profile binding together.

Use [REVALIDATE.md](./REVALIDATE.md) for the sequence. Do not invent missing
evidence or transfer a prior status onto changed inputs.

## 13. Current limitations

- Hugging Face exact-commit acquisition is the only live ingress product.
  Local-directory import requires a separate ADR.
- Physical remote-target acquisition and asymmetric-credential evidence remain
  incomplete.
- Physical cold archive, controller-loss receipt recovery, and remote-rank
  restore are not claimed by deterministic tests.
- Explicit cold recovery configuration is control-plane only; deterministic
  tests make no physical NFS or archive-durability claim.
- Retained Qwen recipe shells require complete re-onboarding.
- The model library does not certify the operator's storage failure domains.

These limitations are visible gaps, not fallback permissions.
