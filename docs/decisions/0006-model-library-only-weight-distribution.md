# ADR 0006: The model library is the only weight-distribution mechanism

- **Status:** Accepted
- **Date:** 2026-08-19
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0002](./0002-subsystem-qualification-boundaries.md),
  [ADR 0003](./0003-explicit-model-preparation-transport.md),
  [ADR 0004](./0004-model-serving-release-validation.md), and
  [ADR 0005](./0005-reject-live-nfs-rdma-serving.md)
- **Decides:** SIM-02 (supported model-storage and distribution surface).
  SIM-03 keep (source-attested unsealed Hugging Face `home add`) is recorded
  in the 2026-08-22 interpretation note; this ADR's library-only mechanism
  is unchanged.
- **Amended by:**
  [ADR 0007](./0007-ordinary-stop-retains-unpinned-hot-views.md)
  (ordinary-stop hot retention only; library-only mechanism unchanged)

## Context

Pulsar carried three weight-distribution surfaces. Replicated per-node
Hugging Face caches were the guided default (`scripts/pull-weights.sh`,
per-rank scans in `scripts/check-weights.sh`, a parallel sealed-identity
witness stack). The model library (`library-hot`) kept one durable home per
exact revision with sealed hot copies on non-home ranks; its reviewed
two-rank scope completed GA on 2026-08-16 while one-rank and legacy-unsealed
use stayed experimental. Live NFS/RDMA serving was rejected by ADR 0005 and
survived only as fail-closed stubs plus roughly 1,700 dead lines behind a
refusal dispatch.

That surface cost more than it returned. A `--weight-source` /
`--weight-mode` axis was parsed and threaded through six entry points.
Maturity labels were duplicated and drifted: `scripts/up.sh` printed
"experimental" for the GA two-rank scope (AUD-02). Every lifecycle,
inventory, and test path branched per mode. Two verification stacks
(replicated witnesses, library witnesses) enforced the same identity
doctrine twice. Three catalog profiles pointed at absolute site paths with
no Hugging Face repository, so the "replicated" launch token also covered
profiles that were never replicated at all.

The library now stands on its own: `home add` (including
`--source-attested`) acquires directly from Hugging Face into the durable
home, preparation is topology-bound eight-stream `ssh-roce` (ADR 0003),
identity is seal/witness-enforced, and cleanup is owned and budgeted.
Nothing in the replicated path provides a capability the library lacks
except serving without a confirmed topology manifest — which contradicts
the membership-truth doctrine this repo already adopted (AUD-01).

## Decision

The model library is the only weight-distribution mechanism. With one
mechanism there is no mode to select, so the selection axis is removed
rather than defaulted.

1. **Every library scope is supported.** Two-rank sealed (GA evidence,
   2026-08-16), one-rank, and legacy-unsealed launches are all supported
   product behavior. The per-scope "experimental" labels are removed. This
   is promotion by decision: the gates that remain open are recorded below
   as accepted risks with follow-up work, not silently waived.
2. **The mode axis is removed, fail-closed.** `--weight-source` and
   `--weight-mode` are no longer accepted anywhere — any value, including
   `library-hot`, exits with an actionable retirement message. There is no
   deprecated no-op and no silent remap.
3. **The replicated path is deleted.** `scripts/pull-weights.sh`, the
   replicated per-rank scan and sealed-replicated witness machinery, and
   their tests are removed. Acquisition is
   `scripts/model-library.sh home add [--source-attested]` only.
4. **Fabric internals are deleted.** The retired live-NFS workflow code,
   its Python backend, and its tests are removed. `./pulsar weight-fabric`
   survives only as `show` / `unmount` / `teardown` for leftover site-local
   state, per ADR 0005.
5. **Non-HF absolute-path profiles are removed from the catalog**
   (`laguna-s-2.1-nvfp4`, `laguna-s-2.1-2node`, `inkling-small-nvfp4`).
   **Current product limit:** serving ingress is an exact Hugging Face
   repository revision (`model_id@commit`), including source-attested
   `home add` of that shape. Privately quantized or directory checkpoints
   are not serveable until a later import ADR. The model library remains
   the only mechanism; Hugging Face is the only current ingress format,
   not a permanent law of the architecture. A local-directory import that
   still computes a complete immutable manifest, assigns a content ID, and
   uses the same hot-view/witness path is future work, not a launch token.
6. **A confirmed topology manifest is a serving prerequisite, including on
   one machine.** The library binds durable homes, hot views, and content
   ids to confirmed topology identity, and topology identity is never
   synthesized. Standalone no-manifest serving retires with replicated;
   `scripts/detect-fabric.sh --write-topology` confirms a one-node
   topology in seconds and the wizard guides it.

| Keep | Why |
|---|---|
| `HF_CACHE` and hub-layout helpers | Durable homes live inside the Hugging Face hub layout |
| Seals, validation bundles, witnesses | Identity doctrine unchanged (ADR 0001/0004) |
| `local-verified-readonly` contract | Mode-agnostic; transport is run provenance (ADR 0004) |
| `io.pulsar.gb10.weight-source` label | Provenance on containers; value is `library-hot` for all new launches |
| Legacy container observability | Containers labeled `replicated` (or unlabeled) remain classifiable and stoppable; they never trigger hot purges |
| ssh-roce eight-stream prepare, NCCL/RoCE inference, topology discovery | Unrelated or already-promoted planes (ADR 0003/0005) |

| Remove | Why |
|---|---|
| `--weight-source` / `--weight-mode` axis | One mechanism; selection is meaningless |
| `pull-weights.sh` + replicated scan/witness stack | Superseded by `home add` + library verification |
| Per-scope "experimental" maturity labels | All scopes supported by this decision |
| Fabric workflow internals and tests | Rejected by ADR 0005; only site teardown remains |
| Absolute-path catalog profiles | No exact `model_id@commit`, no durable home |

## Accepted risks and follow-up work

- **One-rank serving-integration evidence is still pending.** Supporting
  one-rank is a decision, not evidence. Follow-up: capture a physical
  one-rank library serve run into `results/model-library/` and the
  validation ledger.
- **Unattested `home add` is the unsealed Hugging Face ingress.** With
  `pull-weights.sh` gone, every model enters through the library. SIM-03
  (2026-08-22) keeps source-attested unsealed `home add` as a core
  catalog/artifact feature. It is not a reviewed seal and not a non-HF
  import path. Remote-target, asymmetric-credentials, and restore gates
  remain physical validation follow-ups: the control plane exists; those
  cases are not yet covered by physical evidence.
- **Durable-home loss is service loss** for the affected model until it is
  re-acquired. ADR 0001 already records that home-loss resilience requires
  an explicit durable-replica/failover policy on a distinct failure domain;
  that policy is future work and this decision widens its blast radius from
  an opt-in path to the whole product.
- **Legacy-unsealed identity is weaker than sealed** (no reviewed seal;
  `identity_status=legacy-unsealed`). That risk existed before and is now
  first-class rather than experimental. Issuing seals for remaining
  supported profiles continues as ongoing work.

## Supersedes and amendments

- **ADR 0002** listed "promote the catalog subsystem directly into guided
  defaults" as a rejected alternative because subsystem acceptance is
  necessary but not sufficient. This ADR adopts that promotion anyway, by
  explicit decision, converting the unmet combined gates into the recorded
  accepted risks above. ADR 0002's core distinction — subsystem
  qualification is not Model Serving Release validation — is untouched.
- **ADR 0003** framed reviewed `ssh-roce` preparation as applying "when an
  operator explicitly selects" a non-default path, and rejected replacing
  the replicated quick start while the implementation could not complete
  from an empty cluster. `home add` closed that gap. The transport policy
  itself (topology-bound `ssh-roce`, eight streams, no fallback) is
  unchanged; only the "explicit non-default" framing is superseded —
  library preparation is now the only path.
- **ADR 0005**'s rejection of live NFS/RDMA serving stands unchanged. Its
  Keep-table row "`replicated` — guided default" and its remediation
  wording "pointing at `library-hot` or `replicated`" are amended by this
  ADR: remediation now points at the model library alone.
- **ADR 0004** is unaffected in substance: distribution transport is run
  provenance, not release identity, and existing release objects remain
  valid. New evidence can no longer cite `scripts/pull-weights.sh`
  (`acquire-replicated-model`) as a preparation program.
- **ADR 0001** is elevated from the architecture of an opt-in path to the
  architecture of the product. Its consequences — including one durable
  home per exact revision and home-loss = service loss — now apply to all
  serving.

## Consequences

- One launch path, one verification stack, one acquisition flow. Six entry
  points lose the mode axis; lifecycle, inventory, wizard, and tests lose
  their per-mode branches.
- Operators on a fresh machine run `detect-fabric.sh --write-topology`
  once, then `home add` (or the wizard, which guides both) before first
  serve. Weights are still local files at serve time; cold-start locality
  is unchanged. Custom or directory checkpoints cannot be served until an
  import ADR exists.
- Historical evidence is preserved and marked superseded, never rewritten:
  replicated rows in the validation ledger remain as history, and
  `results/weight-fabric/` remains untouched apart from supersession notes.
- Pre-existing containers launched under replicated remain visible in
  inventory as legacy provenance and stop cleanly without model-library
  cleanup; restarting them migrates them to the library.

## Interpretation note — 2026-08-22 (SIM-03)

Source-attested unsealed `home add` remains a core catalog/artifact
feature for an absent brand-new Hugging Face `model_id@commit`. The
operator path is read-only `--plan`, separate confirmation, `--yes`
execution on the planned rank and exact commit, catalog refresh, and
receipt-backed `home verify`. That path creates observed/source identity
only. It does not create a reviewed seal, status, serving permission, or
a Model Serving Release. It is not local-directory or non-HF import
(still a later ADR). Existing receipts and live-directory attachments
stay valid; there is no migration. Remote target execution, asymmetric
credentials, and an actual external restore remain physically pending
and are not reasons to remove the feature.

Rejected SIM-03 alternatives: requiring a reviewed expected seal before
any download (would invert seal-vs-acquisition order with no remaining
unsealed ingress), and deleting source-attested in favor of replicated
`pull-weights` (that path was already removed by this ADR).

## Revisit triggers

Revisit only with a new ADR if (a) a serving-scale model cannot be
represented as an exact Hugging Face `model_id@commit` (local-directory
import), or (b) durable-home availability proves operationally insufficient
before a failover policy lands.
