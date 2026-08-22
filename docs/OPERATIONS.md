# Operations runbook — running this stack day to day

Diagnosis lives in TROUBLESHOOTING.md; this page is procedure. Run cluster
commands on the machine confirmed as rank 0. Remote SSH targets, control
interfaces, HCAs, and rank placement come from `.cluster-topology.json`; names
do not need to follow any pattern.

## Monitoring: never trust /health alone on multi-node

After any remote-rank loss, rank 0's `/health` **keeps returning OK for
~5 minutes** while every request hangs. The only honest liveness probe is a
real completion:

```bash
curl -fsS --max-time 15 http://127.0.0.1:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"<served-name>","prompt":"ok","max_tokens":1}' >/dev/null \
  && echo LIVE || echo "NOT SERVING (health may still say OK)"
```

Wire that (not /health) into anything that pages or restarts. Also useful:
`curl -s :8000/metrics | grep num_requests_` for queue state.

## Root dispatcher (`./pulsar`)

Preferred operator entry point (scripts under `scripts/` remain canonical):

| Command | Action |
|---|---|
| `./pulsar` | Neutral **operator home** (workflow menu; no preflight on entry) |
| `./pulsar wizard` | Serve/switch wizard (doctor + preflight; direct shortcut) |
| `./pulsar models` | Browse cached models/runtime views; explicitly refresh or prepare reviewed models |
| `./pulsar inventory [--json\|--verbose]` | Read-only service/memory inventory |
| `./pulsar start <model> [up args…]` | → `scripts/up.sh` |
| `./pulsar stop <model\|--all> [--node ID]` | → `scripts/down.sh` (ownership-gated) |
| `./pulsar status [model] [--node ID]` | → `scripts/status.sh` (may submit a completion) |
| `./pulsar doctor [--json]` | Read-only host, cluster, and model-library diagnostics |
| `./pulsar weight-fabric [args]` | Leftover live-NFS show/unmount/teardown only (ADR 0005) → `scripts/weight-fabric.sh` |
| `./pulsar help` | Concise usage |

### Model Serving Release policy versus current commands

[ADR 0004](./decisions/0004-model-serving-release-validation.md) defines a
Model Serving Release as the immutable exact-model + serving-recipe +
runtime/image + supported-hardware-geometry tuple and introduces explicit
validation-decision statuses. Any change to one tuple component creates a new
release. Pure descriptor, contract, immutable run-record, evidence-bundle,
reviewed-decision, status-derivation, and supersession schemas are implemented,
and the read-only registry verifies stored objects. Profiles may optionally
bind an exact release with the reviewed `MODEL_SERVING_RELEASE_ID` field.
`scripts/list-models.sh`, the wizard, and `scripts/up.sh` then display the one
unambiguous reviewed effective decision as an advisory release status. A
missing binding displays `No release binding`; absence of a reviewed decision
is not inferred as `Untested`, and ambiguity, registry errors, or a selected
runtime-access recipe mismatch remain visible without blocking launch.

`qwen3.8-27b-fp8` binds the first reviewed ADR 0004 lineage and projects the
advisory status `Testing incomplete`; other current profiles remain neutral.
`STATUS=tested*` remains a separate
legacy evidence/recommendation label; it still determines recommendation
order. Filter that class with `--legacy-tested`. `--validated` is removed
(ADR 0008) and fails closed with that replacement. Neither status field grants
or denies serving. Existing reviewed seals/bundles are not automatically
`Validated`.

The current `./pulsar` commands do not capture or publish ADR 0004 objects or
issue decisions. Local ADR 0004 release planning, attempt composition,
evidence-capture candidate persistence, and issuance staging are separate
maintainer commands and launch nothing; see [MODEL_RELEASE.md](./MODEL_RELEASE.md),
[MODEL_SERVING_RELEASE_CAPTURE.md](./MODEL_SERVING_RELEASE_CAPTURE.md), and
[MODEL_SERVING_RELEASE_ISSUANCE.md](./MODEL_SERVING_RELEASE_ISSUANCE.md).
A staged local proposal is not trusted until repository review and merge.
A schema-valid local decision is not proof of maintainer review or physical
qualification.

Release planning uses
`scripts/model-serving-release-plan.sh build <profile> --artifact-manifest ...
--runtime-envelope ... --criteria ... --model-access-contract ...`; `verify`
rechecks a candidate against the current profile. Non-Hugging-Face primary
models use a public logical ID, public revision, and complete content manifest,
never their local source path. Additional behavior artifacts require explicit
descriptors and use bindings; `--artifact-reference` can normalize an exact
deployment-local argument value to its public artifact key without persisting
the mapping. Output is unreviewed under
`experiments/model-onboarding/`, cannot target `models/`, and grants no status
or serving authority. The explicit runtime/hardware envelope is a structural
contract, not physical qualification.

The corrected schemas remain version 1 because no ADR 0004 object was issued
or persisted before the correction. Existing legacy schema-1 seals/bundles and
raw evidence remain untouched. Criteria use canonical scopes: stability,
accuracy, throughput, latency, and strict same-boot are
`model-qualification`; serving integration is `serving-integration`; and
provenance/security plus physical geometry are `release-promotion`.
Catalog/artifact preparation may establish exact content and the qualification
barrier, but it cannot satisfy a validation criterion.

A decision automatically considers every applicable observation. The pure
builder accepts exceptions only through `criterion_exclusions`; persisted
results use `included_run_record_ids` and evidence-backed
`excluded_run_records`. Pass+fail and pass+inconclusive are inconclusive,
fail+inconclusive is fail, and all-pass is pass. A relative
performance baseline binds the reviewed predecessor contract, bundle, decision,
and run whose relevant criterion passed; the predecessor does not need to be
globally `Validated`. Runtime compatibility and architecture/geometry checks
are structural and never replace physical DGX evidence. Supersession must be
later and acyclic. Predecessor and effective-supersession registries are
caller-supplied validation inputs, not trusted storage. Command evidence uses
allowlisted programs, SHA-256-shaped version identities, closed operations and
resources, typed criterion/site references, and value-free `environment[]`
descriptors. Each post-barrier non-preparation attempt declares a nonempty
scope-compatible `attempted_criterion_ids` set that its observations cover
exactly; incomplete attempts use inconclusive observations. Structural privacy
rejection does not replace the mandatory publication privacy audit.

The supervised skill is `skills/pulsar-model-onboarding/`. It composes
available artifact reuse, distribution, verification, launch, test, evidence,
and cleanup subsystems, including explicitly selected Experimental ones. It is
orchestration only: it never issues a seal or validation decision, assigns
status, binds a profile to a release, writes the trusted registry, promotes a
path, or claims physical behavior. Current automated mapping covers only
strict same-boot and absolute throughput/latency. An unsealed profile serves
with its honest `legacy-unsealed` label but is not an exact ADR 0004
qualification attempt. For an absent brand-new unsealed Hugging Face
repository, the skill may plan and, after a separate confirmation, run the
source-attested exact-commit acquisition service. Reuse of that home requires
receipt-backed offline full verification against the receipt attached to the
exact live directory. An unknown, restored, replaced, or otherwise unbound
home still requires full verification against a reviewed expected manifest
independent of the observed tree; catalog state and a self-observed manifest
alone are insufficient. The acquisition creates catalog/artifact evidence
only, not a seal, status, decision, serving permission, promotion, or physical
claim. Its recovery journal lives
under `experiments/model-onboarding/workflows/`, separate from release-plan output.
Deterministic skill and journal tests make no physical DGX claim and create no
release decision.

**Invalid habit:** `./ wizard.sh` (space after `./`) makes Bash run the directory
`./` with `wizard.sh` as an argument, yielding `-bash: ./: Is a directory`.
Use `./pulsar wizard` or `./wizard.sh`.

## Operator home

`./pulsar` with no arguments opens `scripts/home.sh` immediately — no doctor,
inventory, weights, image, or model preflight until you pick a workflow.

Menu (default cursor: status):

1. **Current system status** — `scripts/quick-status.sh` (read-only)
2. **Serve or switch a model** — enters `wizard.sh` (its doctor/preflight)
3. **Stop a serving model** — inventory-safe active managed only; library-hot
   services choose retain vs free, then confirm → `down.sh`
4. **Models & storage** — cached identity, placement, runtime views, and findings
   (browsing is read-only; refresh/preparation are explicit; the model library
   is the only weight mechanism)
5. **Maintenance** — optional clean of **stale** `safe_to_stop` managed containers
6. **Diagnostics** — run doctor, detailed inventory (read-only)
7. **Exit**

Read-only actions and cancelled subflows return home. Gum Escape/cancel and EOF
exit cleanly without mutations. `--all` is not offered in interactive stop or
maintenance; there is no automatic cleanup on doctor or startup.

### Models & storage semantics

`./pulsar models` and Home **Models & storage** call
`scripts/model-storage.sh`, an interactive projection of the cached
model-library health report. The model library is the only weight mechanism
([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md));
browsing labels each profile's exact scope. It shows cached age
and topology compatibility, profile and exact model/revision/manifest identity,
durable-home and primary state, per-rank runtime
source/retention/identity/witness state, active references, and structured
findings. Rank labels are generic; site identities and paths stay out of the
public report.
If the cached catalog no longer matches the confirmed topology, model identity
remains visible but home and primary node placement is marked stale and hidden
until the operator explicitly refreshes the catalog.

Browsing and **Recheck catalog health** only run
`scripts/model-library.sh health --json`. They do not refresh the catalog,
prepare or move files, start a model, pin or purge hot copies, run repair, or
delete a durable home. `attention` and `unavailable` reports remain readable;
invalid health JSON fails closed with no action. Catalog absence or health
findings do not affect already-running services, but new preparation is
blocked until they resolve. Pinning remains a retention choice,
not durable-home-loss resilience, and catalog/serving health is not model
qualification or release promotion. Mutating model-library operations remain
direct CLI workflows except for the two bounded actions described below.

**Refresh distributed catalog** is a separate default-no confirmation. It
delegates to the atomic all-confirmed-rank catalog refresh and then obtains a
new sanitized health report. It does not move model bytes.

**Prepare for two-rank serving** or **Prepare for one-rank serving**
appears only for a serving profile that is associated with the exact
catalog entry and carries a reviewed expected seal. Before confirmation the
view shows the exact model revision and manifest,
durable-home dependency, serving node count, approximate non-home storage, and
fixed transfer policy. Multi-node preparation uses SSH over the confirmed RoCE
plane, eight streams, and no fallback. Confirmation delegates to:

```bash
scripts/model-library.sh prepare <profile> --yes
```

Multi-rank profiles default to SSH over the confirmed RoCE plane with eight
streams. A one-node profile targets its durable-home rank and uses
`ssh-control` with one stream (no bulk transfer). Management-network copy on a
multi-rank profile requires explicit `--transport ssh-control`.

The preparation service remains authoritative: it rechecks topology and
primary placement, full-verifies the expected seal, performs exact all-rank
storage admission, creates only non-home sealed-hot copies, publishes witnesses
only after the all-rank barrier, and rolls back or leaves explicit incomplete
state on failure. The interactive surface always obtains fresh health after the
attempt. It provides no validation-status override, transport picker, fallback,
or automatic launch. Success means the artifacts are prepared, not that a model
was started, qualified, assigned a release status, or made the guided default.

### Serving-wizard library path

`./pulsar wizard` has no storage-mode choice. The model library serves every
profile (ADR 0006). For a reviewed (sealed) profile the wizard reads current
catalog health, displays exact revision/manifest and durable-home dependency,
and either proves the selected views ready, offers a guided one-time `home add`
acquisition when no durable home exists, or offers the bounded preparation
above. An unsealed profile checks its prepared views directly and offers
explicit preparation; its acquisition is the separate source-attested two-step
CLI flow. Nothing refreshes the catalog automatically and there is no fallback
path. After preparation the wizard requires exact ready views before the
normal weight preflight. Starting remains a separate final confirmation.

For one-node catalog serving, select the durable-home node; Pulsar refuses a
non-home rank rather than creating a second hot copy. Preparation uses
`ssh-control` with one stream as a no-bulk-transfer local-view operation. For
multi-node profiles, non-home copies use `ssh-roce` with eight streams. Neither
case changes Model Serving Release status.

### Quick status semantics

Home status is **not** `scripts/status.sh`. It consumes inventory JSON, probes
only `GET /v1/models` for API **model advertisement**, and never submits a
completion. Advertisement is **not** an inference health claim. Concise fields:
active managed conf/ranks, API models, MemAvailable + MemTotal for every
confirmed rank, managed GPU/unified per rank when measured, aggregate remote
reachability, unmanaged GPU count + aggregate MiB, and stale managed count
(nonblocking / no model memory). Optional follow-ups: refresh, detailed inventory, explicit full
smoke (`status.sh`, may complete), back. Machine-readable: `scripts/quick-status.sh --json`.

### Interactive stop and maintenance

Stop lists only **active** services with `ownership=managed`, `safe_to_stop=true`,
and proven complete ownership. Unknown, legacy, mismatch, incomplete/unproven,
foreign GPU, and any remote-unobservable multi-node services are excluded. After
selection, final confirmation is required; only `scripts/down.sh <conf>` runs
(revalidates labels/IDs). Decline → no mutation. When the stopped service's
labels prove `weight-source=library-hot`, ordinary stop retains unpinned prepared
views ([ADR 0007](./decisions/0007-ordinary-stop-retains-unpinned-hot-views.md)).
The durable home is still required. Interactive stop offers retain (default)
versus free, and states the restage consequence when a non-home byte count can
be proven — for example “Free 167 GiB now; next start requires a full restage.”
`--retain-weights` keeps unpinned views even when site policy is `purge`.
`--pin-weights` protects retained views from a later unforced purge and is used
for a confirmed same-profile restart. `--purge-hot` is the explicit
capacity-recovery action and may remove a pin. Site
`PULSAR_HOT_STOP_POLICY=retain|purge` selects the named-profile CLI default;
unset means retain; invalid values fail closed. `down.sh --all` never
auto-purges. Legacy containers launched before ADR 0006 (label
`weight-source=replicated`, or unlabeled) stop cleanly and never invoke
model-library cleanup; restarting them migrates them to the library.

Maintenance “Clean stale stack-managed containers” lists only **stale** +
`safe_to_stop` managed entries, explains they are nonblocking, requires
confirmation, and delegates each conf to `down.sh`.

### UI colors

Shared helpers in `scripts/ui.sh` (home + wizard). Two deterministic modes:

1. **Plain Bash menus (uncolored)** — forced by `GUM=0`, `NO_COLOR`,
   `PULSAR_COLOR=never`, or `TERM=dumb` (also empty `TERM`). Gum is not invoked
   at all (same path as `GUM=0`), so Charm pink/purple defaults cannot appear.
   Non-interactive stdin/stderr also selects this EOF-safe path instead of
   starting a hidden Gum TUI that can wait forever.
2. **Color-enabled Gum** — only when Gum is available and color is allowed.
   Always overrides Charm defaults with terminal-palette blue: bright blue
   **12** for choose cursor/header/**selected** and confirm prompt; confirm
   selected button blue bg **4** + bright fg **15**. Ordinary list items have
   no colored background.

| Variable | Effect |
|---|---|
| `GUM=0` | Plain Bash menus (uncolored); Gum not used |
| `GUM_BIN` | Override gum binary path (only when color-enabled) |
| `NO_COLOR` / `TERM=dumb` / `PULSAR_COLOR=never` | Force plain Bash menus (not “Gum with no flags”) |
| `PULSAR_ACCENT` | Override accent when Gum is color-enabled (default `12`) |

Plain menus never emit raw ANSI.

### Human output conventions

Human readability is a primary CLI requirement. Snapshot-style commands use
short semantic sections, aligned labels, and width-aware hanging indentation;
interactive choices stay on one compact line and show detail after selection.
Chronological launch/validation output remains log-oriented because source and
event order matter there.

`scripts/terminal_format.py` owns the shared Python field/wrapping behavior,
while `terminal_width` and `print_hanging` in `scripts/lib.sh` cover Bash
diagnostics. Human views must remain meaningful without color and are tested at
narrow terminal widths. Automation must consume the documented JSON modes, not
parse the human presentation.

## Inventory and ownership

`scripts/inventory.sh` (also `./pulsar inventory`) is **read-only**. It never
stops containers. JSON contract is `schema_version=1` with:

- `services[]`: `conf`, `state` (running/partial/degraded/stale/…), `ownership`
  (managed/legacy/mismatch/unknown/mixed), `safe_to_stop`, `complete`,
  `observability`, ranks, estimated footprint, optional GPU memory
- `nodes.*.mem_available_gib` / optional additive `mem_total_gib`; names include
  `head`, legacy `worker`, and `rank-N` for additional confirmed ranks
- `unmanaged_gpu_processes[]`: diagnostics only — **no kill action**
- `worker.status`: compatibility aggregate for all other confirmed nodes (`ok`,
  `unset`, `unreachable`, or error)

Machine-readable JSON and Docker labels retain vLLM's zero-based `rank`
field: rank 0 is this node, rank 1 is the second cluster node, and so on.
Human-facing output uses those node names instead.

**`safe_to_stop` is true only** when every *observed* rank has
`io.pulsar.gb10.managed=true` and conf/rank labels that map consistently to a
repo profile. Lifecycle scripts (`down.sh`, cluster stop) **revalidate** labels
and IDs before remove. Unlabeled legacy, mismatch, unknown, incomplete, or
remote-unobservable situations are never auto-stopped.

Live inventory fails closed if rank 0 Docker cannot answer `info`, enumerate
containers, or return a valid snapshot. It probes every other confirmed node;
any unreachable rank, Docker error, or enumeration error is accumulated in the
compatibility `worker.status`/reason and blocks automatic multi-node
stop/replacement. An operational failure is never converted into an empty
“nothing is running” inventory.

Weight readiness means more than “the cache directory exists.” Legacy-unsealed
HF profiles require `refs/main` to resolve to a snapshot with a readable
non-empty `config.json` and at least one non-empty weight file. Sealed
profiles ignore `refs/main`, resolve the reviewed commit directly, and require
the exact manifest identity through a rank-local witness or visible full-SHA
fallback. `.incomplete` markers and local shard indexes that reference
missing/empty files fail preflight. Multi-node profiles are checked on every
exact active rank. Docker/SSH failures are reported as operational failures and
never offered as a download/pull problem.

The memory checker grants “already loaded” mode only to a running exact-name
service whose stack ownership, selected conf, and every expected rank are
proven by labels. API model identity or an unlabeled lookalike is insufficient.

Human default output uses width-aware stacked sections for active + actionable
managed stale/mismatch services. `--verbose` also includes inactive
unknown/legacy detail, IDs, probe sources, and full process paths; `--json` is
always full.

## Model switch (wizard)

`./pulsar wizard` (not the no-arg home) runs doctor once, offers cluster
discovery/confirmation when no remote topology is active, then enters a
**selection loop** (“Choose another model” does not re-run doctor). The menu is
capacity-aware and status-transparent: it includes every serving-purpose
profile with `NODES` no greater than confirmed capacity, displays its advisory
status and notes, and orders legacy evidence-backed recommendations first. It
never derives a new TP/PP geometry from the discovered node count.

After a one-node model is selected, the wizard evaluates every confirmed
physical node independently. A candidate must retain the confirmed immutable
node ID, be reachable through its BatchMode SSH endpoint when remote, have a
working Docker daemon, and avoid a hard failure under the model's cold-start
memory policy. The placement screen shows hostname, free memory, Docker state,
Pulsar/unmanaged occupancy, and the recommended idle target. Artifact checks,
port ownership, API health, and the final confirmation are then scoped to that
selected node. Running services on non-overlapping nodes remain visible but do
not become blockers.

Before each start plan it consumes inventory JSON + `check-memory.sh` and shows
a short target summary (not raw JSON). Decision highlights:

1. **Same profile running + API healthy** — Keep running (recommended), Restart
   (stop only after final confirm), Show status, Choose another model. Never
   silent replacement.
2. **Different complete managed blocker** — names conf/state/ranks/memory and
   `safe_to_stop`; offers stop listed stack-managed service(s) → recheck →
   start, keep current, choose another, or diagnostics.
3. **Partial/degraded managed** — explains observed vs expected ranks; cleanup
   only if every observed rank is inventory-safe and remote-rank observability
   permits lifecycle revalidation. Never implies completeness. Reinventory
   after any stop.
4. **Unknown/unmanaged GPU or unknown port owner** — read-only diagnostics;
   wizard **will not stop** it. Exit / choose another / diagnostics only.
5. **Stale managed** — does not hold model memory; safe cleanup only when it
   blocks the selected exact name and is `safe_to_stop`. Unknown stale
   summarized and left alone.
6. **Memory WARN** after cleanup — shows free/footprint/need; explicit continue
   allowed. **Memory FAIL** — no launch, no continue-anyway. If a previous
   service was transactionally stopped, offer restoration of its exact captured
   contract, choose another model, or exit stopped with recovery state retained.
7. **Launch fails after replacement** — report failure; offer the same exact
   rollback. Profile defaults, storage source, and placement are never guessed.
   There is no auto-restart loop.

**Stops are always deferred** until after the final start/replace confirmation.
Immediately before stopping one running service, the wizard requires complete
fresh inventory plus current launch-contract/spec labels. A `library-hot`
service whose identity is a reviewed `match` records exact placement and
weight policy in a short-lived site-local transaction; ephemeral views are
pinned before stop. A complete, safe-to-stop library-hot service without that
match (`legacy-unsealed` or `unvalidated`, including first-run Nemotron) is
stopped without a rollback transaction — exact restore is unavailable, as with
a leftover pre-library launch. Multiple running stop targets, partial
services, old unlabeled services, drift, or failed retention leave the
service running and make automatic replacement unavailable. To adopt an older
managed service, stop it explicitly after reviewing `./pulsar inventory`, then
start it once with the current Pulsar release.

After any stop, inventory + cold memory preflight run again (never assume
reclaim). If the wizard exits or is interrupted, the next `./pulsar wizard`
detects the unfinished transaction. It restores only when the saved contract is
still exact; ambiguous live state is left untouched with remediation to inspect
or explicitly stop the new service before retrying. A leftover record captured
under the removed replicated mechanism cannot be restored. The wizard reports
whether that previous profile is running, stopped, or ambiguous, then offers a
confirmation-gated archive. The original file is moved into a timestamped
`recovered/` directory next to the live transaction; exact rollback is not
attempted. Noninteractive runs print the live path and
`python3 scripts/replacement_transaction.py archive --path <file> --yes`.
A confirmed rollback keeps the record until temporary retention is restored. A
successful replacement closes the rollback record; any failure to release or
purge the previous hot view is reported with a direct model-library remediation
command. The record is not permanent history. Credentials and container argv are
never stored; the opaque launch digest records auth presence and supported
runtime overrides.

## Start / stop

- Preferred: `./pulsar start <name>` / `./pulsar stop <name>` (or
  `scripts/up.sh` / `scripts/down.sh`).
- To place a one-node profile explicitly, pass `--node <node-id>` to
  `start`, `status`, or `stop`; get the immutable ID from
  `./pulsar inventory --json`. Named status/stop without `--node` probes all
  confirmed nodes and proceeds only when it proves one unique placement.
- Single node low-level: `./serve.sh <name> -d`. Do **not** `docker rm -f` by
  name unless inventory proves ownership — prefer `./pulsar stop <name>`.
  `docker-compose.yml` is an unsupported historical sketch, not an equivalent
  operator path. It bypasses profile-contract/placement gates, exact
  revision/seal identity, read-only runtime views, preflight, and Pulsar
  ownership/launch/topology labels; home, wizard, and `down.sh` will not manage
  it. Do not use it for lab serving.
- Multi-node exact profile: `cluster/preflight.sh <name>` then
  `cluster/start-cluster.sh <name>`. **ALWAYS `cluster/stop-cluster.sh <name>`
  or `./pulsar stop <name>` before relaunch** — a surviving cluster node can
  retain rendezvous or RDMA state and make the new rank 0 hang.
- After multi-node health, `start-cluster.sh` runs `validate/warmup.py` once
  (short+medium prompts, c=1 and c=4, stream and non-stream). That pays
  DSpark/Triton/block-FP8 JIT so the first real client is not the cold path.
  Skip with `--skip-warmup` (falls back to a single smoke completion).
  Manual: `python3 validate/warmup.py --url http://127.0.0.1:8000 --model <served>`.
- **Flagship DeepSeek defaults** (`models/deepseek-v4-flash.conf`) target
  **few long agent sessions** (≤5 concurrent, 500K client cap, tools/code),
  not high-QPS chat: 20 GB/rank KV, `max-num-seqs 5`, batch 16384, tool+
  reasoning parsers. Before resizing KV further: `drop_caches` on both exact flagship ranks,
  step only (never ≥27.5 GB/rank — known OOM), read boot "GPU KV cache size",
  soak. Details in the conf header and docs/RECIPES.md / docs/MODELS.md.
- One big model per node, ever. gpu-mem-util 0.85 leaves ~18 GiB for the OS
  on a 121 GiB shared pool; a second workload swaps the box.
  Before starting after other work: `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`.

## What "slow startup" looks like when it is fine

| Model | Cold load to healthy |
|---|---|
| qwen3-1.7b | ~2 min |
| qwen 27B / nano | ~4-9 min |
| deepseek-v4-flash (167 GB, both nodes) | ~12-15 min |

`--health-start-period` is 900 s for this reason. Watch
`docker logs -f` for `Loading weights took ...` before suspecting a hang.

## Node-loss playbook (measured behavior)

1. In-flight requests hang silently — client timeouts are your only signal.
2. ~5 min later: `sample_tokens RPC timed out` → engine dead; `/health`
   finally fails; container still shows "Up" (API alive, engine dead).
3. **There is no recovery.** Do not wait for one:
   `cluster/stop-cluster.sh <name> && cluster/preflight.sh <name> && cluster/start-cluster.sh <name>`
   (or `./pulsar stop <name>` then the same preflight and start; ~15 min
   back to serving). `stop-cluster.sh` requires `<name>` or `--all`.

If a hang does NOT follow a node event, walk TROUBLESHOOTING.md (multi-cause
`sample_tokens` RPC timeout tree) before restarting in a loop.

## Logs

- Rank 0: `docker logs vllm-cluster-<name>` (or `vllm-<name>` single-node).
- `./pulsar inventory --verbose` shows every observed rank and node placement.
  Use the corresponding SSH target from the confirmed topology for remote
  `docker logs`. `start-cluster.sh` automatically tails every rank on startup
  failure.
- On any first boot / after upgrades, grep for
  `attention backend|MoE backend|Unknown vLLM env`; vLLM can drop environment
  variables silently across versions.

## Staging images and weights to exact ranks

```bash
# Images stage per-rank; weights come from the model library (ADR 0006):
# home add → catalog refresh → prepare (see "Prepare model for serving").
scripts/sync-image.sh <profile> --pull --yes

# A one-node profile selected for a remote confirmed node:
scripts/sync-image.sh <profile> --node <node-id> --pull --yes
```

The model library downloads one durable home and prepares sealed hub views
tree to every other node required by that profile. For a profile with
`EXPECTED_MODEL_SEAL`, it requests the reviewed commit explicitly, full-hashes
the controller snapshot and every copied rank, and writes a rank-local witness
outside the repository. A configured mismatch fails closed. Unsealed profiles
retain the legacy mutable-`refs/main` workflow.
Its normal output uses readable stages and hostnames; set `PULSAR_VERBOSE=1`
to expose raw Hugging Face and rsync diagnostics. The image helper loads every
missing required node and repairs digest references that a bare `docker load`
can omit. NFS/catalog profiles are not copied: mount the same readable path on
every required node and run `scripts/check-weights.sh`.

### Confirmed SSH identity and endpoint drift

Topology schema 2 binds each selected transport address to the confirmed
node's stable SSH alias, enrolled host-key set, and immutable node ID. Enroll
and check it explicitly while the cluster is idle:

```bash
scripts/topology-ssh-trust.sh enroll
scripts/topology-ssh-trust.sh check
scripts/doctor.sh
```

Enrollment writes the gitignored `.cluster-ssh-config` alongside the topology.
The shared loader rejects schema 2 if that generated config is missing or
stale. `doctor` reports the selected endpoint, expected/observed node identity
and key fingerprints, and drift class. It is read-only: it never rewrites
topology or OpenSSH trust, never accepts a replacement key, and never creates
or changes `HF_CACHE`. A missing cache is a warning because NFS-backed models
do not require it; an existing wrong-type or inaccessible cache is blocking.
Model download or preparation owns cache creation. Use this remediation policy:

| Doctor finding | Safe response |
|---|---|
| Endpoint changed; node ID and key still match | Run `scripts/detect-fabric.sh --json`, review all rails, explicitly write topology, then re-run SSH trust enrollment |
| Alias changed; node ID and key still match | Confirm the rename, explicitly rewrite topology, then re-run SSH trust enrollment |
| Host key changed; node ID still matches | Stop. Verify reimage/key rotation out of band, update normal OpenSSH trust deliberately, then run `scripts/topology-ssh-trust.sh enroll --accept-key-change` |
| Node ID changed at the old alias/address | Treat it as a replacement node; re-qualify and confirm membership |
| Rail IP presents another node's key | Stop immediately; repair duplicate/stale addressing or the topology mapping |

Do not “fix” a changed-key failure by disabling strict checking or broadly
deleting `known_hosts` entries. If replacement is legitimate, preserve the old
and new fingerprints in the incident/change record and enroll only the key
verified through an independent channel.

### Retired live NFS serving

**Live fabric (NFS/RDMA under vLLM):** retired as a serving runtime source
([ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md)); its workflow
implementation was removed with the whole weight-mode axis
([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)).
`--weight-source`/`--weight-mode` fail closed everywhere. Leftover site
mounts: confirmation-gated `scripts/weight-fabric.sh show|unmount|teardown`
only. Historical notes: [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md).

### Removed compatibility aliases (ADR 0008)

These tokens still parse so the error names the replacement, then the command
exits 2. They are not unknown-argument failures.

| Removed | Replacement |
|---|---|
| `--force` on `up.sh`, `serve.sh`, `start-cluster.sh` | Drop the flag. Status labels never block serving. |
| `--allow-unvalidated` | Drop the flag. Seals still fail closed. |
| `list-models.sh --validated` | `--legacy-tested` (historical `STATUS=tested*`). Not ADR 0004 `Validated`. |
| `model-library.sh catalog list --validated` | `--reviewed-identity`. Not ADR 0004 `Validated`. |
| `model-library.sh activate` | `prepare` |

`--force-unpin`, leftover `weight-fabric.sh show|unmount|teardown`, topology
schema 1 as `detect-fabric` output, and hot schema-1/2 repair are not in this
table. `HEAD_IP`/`WORKER_IP` never confirm membership and do not construct
topology.

N≥2 `check-image.sh` JSON emits `rank-unreachable` / `rank-docker-error` /
`missing-on-rank` (or `missing-both`). One-node `missing-on-head` /
`missing-on-target` still mean a different repair (`sync-image --pull`) than
`missing-on-rank` (sync without `--pull`). Do not collapse those names.

**The model library (federated catalog + local hot staging):** the only
weight-distribution mechanism (ADR 0006); every scope — two-rank sealed,
one-rank, legacy-unsealed — is supported. The fixed transport policy for
reviewed multi-rank preparation is recorded in
[ADR 0003](./decisions/0003-explicit-model-preparation-transport.md).

"Prepare model for serving" is the operator-facing term for resolving the exact
model, creating rank-local runtime views from an **existing** durable home,
transferring only non-home bytes, and verifying every rank. Preparation does
not create the durable home, start a serving container, or qualify the model.
Public `activate` is removed (ADR 0008); use `prepare`.

Typical reviewed multi-rank flow:

```bash
# Required before topology-bound SSH-over-RoCE preparation.
scripts/topology-ssh-trust.sh enroll
scripts/topology-ssh-trust.sh check
scripts/model-library.sh catalog refresh
scripts/model-library.sh catalog list --reviewed-identity
# Reviewed multi-rank sealed profile: validation status is not an override.
scripts/model-library.sh prepare <multi-rank-sealed-profile> --yes
scripts/up.sh <multi-rank-sealed-profile>
# optional after stop:
scripts/down.sh <multi-rank-sealed-profile>                 # retain unpinned non-home hot
scripts/down.sh <multi-rank-sealed-profile> --pin-weights  # protect from later unforced purge
scripts/down.sh <multi-rank-sealed-profile> --purge-hot    # free hot disk budget
```

A reviewed single-rank profile has no non-home target and therefore no RoCE
transfer. Prepare only its local durable-home runtime view:

```bash
scripts/model-library.sh prepare <single-rank-sealed-profile> \
  --backend copy --transport ssh-control --yes
```

Legacy-unsealed profiles are outside ADR 0003's fixed transport and stream
policy. Their low-level preparation path needs no validation-status override;
choose and record its transport and stream count as experiment inputs rather
than inheriting the reviewed-profile recipe. `--allow-unvalidated` is removed
(ADR 0008); drop the flag. It never bypassed a configured seal mismatch.

Catalog refresh inventories existing durable homes; it does not download model
bytes or create a primary home. Preparation therefore requires an eligible
exact home to exist already; `home add` is the acquisition path (the wizard
guides it for sealed profiles). `check-weights` / `up.sh` print the same
commands: sealed `home add <profile> --yes`, the unsealed plan-then-`--yes`
sequence, `cleanup-recommend` / `catalog primary set` when duplicate homes
have no primary, or `prepare` when a home already exists. For a one-node
`--node` placement, an unreachable rank tells the operator to restore SSH
(not restage), and a non-home rank names the durable-home node instead of
`prepare`. For sealed profiles:

```bash
# Optional --node RANK|NODE_ID overrides most-free-space placement.
scripts/model-library.sh home add <sealed-profile> --yes
# Registration remains explicit; home add never refreshes automatically.
scripts/model-library.sh catalog refresh
scripts/model-library.sh catalog show <sealed-profile>
```

For a brand-new unsealed profile whose repository is absent everywhere, plan
first. The plan is read-only and resolves the selector to an exact commit:

```bash
scripts/model-library.sh home add <profile> \
  --revision <selector> --plan --json
```

Review the exact commit, complete upstream file/byte counts, selected rank, and
serving geometry. After a separate large-download confirmation, execute with
the exact commit and reviewed rank from that plan, not a mutable branch or tag:

```bash
scripts/model-library.sh home add <profile> \
  --revision <exact-commit-from-plan> \
  --node <selected-rank-from-plan> --yes --json
scripts/model-library.sh catalog refresh
scripts/model-library.sh catalog show <model_id@exact-commit>
scripts/model-library.sh home verify <model_id@exact-commit> --json
```

The selected rank must already have resolved public metadata before the plan
is shown, and it downloads using its own Hugging Face authentication. Pulsar
does not accept, print, persist, or move a token. Model and transient Xet/asset
bytes stay in private same-filesystem staging until complete inventory and
SHA-256 verification, a repeated all-rank absence check, immutable-receipt
publication, atomic no-replace home publication, and a private current-home
attachment succeed. Acquisition does
not refresh, prepare, launch, or grant reviewed authority. `home verify` is
offline and rehashes the complete tree against the attached receipt before
later reuse. A matching tree without that live-directory attachment is
treated as unknown and still requires a reviewed expected manifest; that
recovery path hashes attested empty snapshot files the same way receipt-backed
verify does.

`home add` inspects every confirmed rank and refuses existing repository paths,
unobservable nodes, insufficient capacity, missing target-side Hugging Face
CLI, or an ineligible/out-of-geometry explicit node. A one-node profile may use
any confirmed rank as its sole serving placement; without `--node`, the most-
free-space eligible rank is selected. Multi-node placement remains limited to
the profile's exact serving ranks so preparation retains the one-home plus N−1
hot-copy contract. The chosen rank needs upstream access and
its own Hugging Face authentication when the repository is gated. Pulsar checks
the target's PATH and its managed `$HOME/.hf-cli/venv/bin/hf` installation. It
downloads the exact commit into a private directory on the destination
filesystem, repeats the cluster-wide duplicate check, and performs the
applicable sealed or source-attested full verification before atomically
publishing one durable HF repository. It never copies
through the controller, chooses a second node after failure, creates hot data,
prepares a view, launches, or changes validation status. Download/verification
failure removes only the current plan's staging directory. If cleanup reports
incomplete, inspect that exact `.pulsar-acquire-*` directory before retrying.

Source-attested crash and retry behavior:

- A leftover exclusive writer temp next to receipts or attachments is ignored
  during enumeration and does not block a later write.
- A receipt without a published home is an orphan history record. Retry may
  reuse that receipt, publish, and attach.
- A published home without a current attachment is unbound. Do not reconstruct
  the attachment from matching bytes. Remove the home with supported
  `home remove` and re-add, or use a reviewed expected manifest.
- Supported `home remove --yes` detaches the current pointer before the
  directory mutation and keeps receipts. `home check` and a declined remove
  do not detach. If removal fails after detach, the surviving home is unbound.
- Legitimate remove and re-add writes a new attachment for the new directory
  identity. Older compatible receipts remain history.

`pin` marks non-home hot content as purge-protected. Cold stage-only hot may
be fully self-contained. Warm-home preparation is deliberately different: the
home rank uses a zero-copy symlink/runtime view of its authoritative durable HF
cache, and only non-home ranks own sealed-hot copies. Home-rank hot
materialization is ruled out by
[ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md).

A warm-home pin permits restart without cold storage, a transfer plane, or
catalog refresh while the durable home remains. It does **not** claim survival
after home loss. Do not remove or unmount the home while a running or pinned
instance depends on it. Home-loss resilience requires an explicit durable
replica on another failure domain and supported failover.

**What model-library checks prove:** interpret each operator surface in its own
qualification scope:

| Check or action | Evidence scope | Claim boundary |
|---|---|---|
| `health`, catalog refresh, primary state | Catalog/artifact inventory and policy state | Does not prove that a model can serve correctly |
| `home add` exact download, verification, receipt when source-attested, and publication | Catalog/artifact acquisition | Does not prepare a runtime view, qualify the model, grant reviewed identity, or promote the storage path |
| Preparation, seal/manifest verification, witness, pin/purge/repair | Catalog/artifact identity and lifecycle | Does not qualify runtime behavior |
| Exact-source launch, health, warmup, completion smoke, owned stop | Serving integration | Does not prove accuracy, determinism, performance, context, or soak |
| `validate/run-gates.sh` and profile-specific physical gates | Model qualification for the exact image/configuration/geometry | Does not independently prove another storage policy safe. Ordinary invocation stays human-compatible and does not require a release plan. Optional `--measurement-dir` writes closed compare/bench measurement files under `results/` or a safe explicit outside path; optional `--invocation-plan` is an explicit contract-driven bench overlay and fails closed instead of changing the default sweep. |
| Validation status | Evidence-derived release decision | Describes confidence/results; never grants or denies serving |
| Recommendation/default storage policy | Combined release/promotion | Requires every applicable subsystem gate; does not hide other labeled choices |

A runtime qualification failure does not make unchanged catalog bytes,
placement, transfer, or cleanup unhealthy unless evidence demonstrates that
connection. It still blocks a release or guided-path claim that requires the
failed runtime gate. Conversely, catalog health and a successful completion
never substitute for model qualification. See
[ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md). Legacy
catalog/launch commands and JSON do not store these scopes as independent
status dimensions. The ADR 0004 Validation Contract criteria, evidence
artifacts, and run records do carry a validated `qualification_scope`; those
pure objects are not yet captured, persisted, or projected by operator
commands.

**Duplicate durable homes:** refresh first, then inspect the persistent
exact-revision primary state. Pulsar never silently chooses among duplicates:

```bash
scripts/model-library.sh catalog refresh
scripts/model-library.sh catalog primary list
scripts/model-library.sh cleanup-recommend

# Choose one complete home by confirmed rank or stable node ID.
scripts/model-library.sh catalog primary set '<model_id>@<revision>' --node RANK

# Re-run after selection; it now prints check/remove commands only for extras.
scripts/model-library.sh cleanup-recommend
```

The selection is stored atomically in the site-local catalog and survives
normal refresh. Setting it verifies the catalog rank/node pair against the
currently confirmed topology. If the selected node no longer reports that
exact complete revision, the primary is `stale` and resolve/prepare fails
closed; choose another complete home explicitly or repair the selected home.
Deleting the site catalog also deletes this local policy, so the next refresh
returns a duplicate to operator-required state rather than reconstructing a
choice from old `primary` flags.

`catalog primary clear '<model_id>@<revision>'` deliberately removes the
explicit choice. With duplicate homes this makes the revision unavailable
until another primary is selected. Primary selection changes future
resolution; it does not stop a running service, move model bytes, create a
replica, or authorize deletion.

**Catalog health:** use the cached, read-only view before library maintenance:

```bash
scripts/model-library.sh health
scripts/model-library.sh health --json
```

`healthy` and `not-configured` exit zero. `attention` and `unavailable` exit
nonzero after printing a complete schema-1 report. The command never refreshes
the catalog or witness, hashes model bytes, or repairs automatically. Public
JSON exposes rank numbers and opaque repair IDs but no hosts, addresses, node
or topology IDs, absolute paths, filesystem identity, or witness IDs. Doctor
shows the same findings as warnings; running services are unaffected, but new
preparation is blocked until they resolve.

The interactive **Models & storage** surface (`./pulsar models`) opens this
cached report without scanning ranks. **Recheck catalog health** remains
read-only. **Refresh distributed catalog** is a separate action that shows the
cached age and scope, defaults its confirmation to no, and only then runs the
same `catalog refresh` command shown above. A successful refresh is followed by
a new sanitized health report. Missing/stale topology, unreachable ranks, or
invalid scans fail closed; model files are not changed. Refresh never downloads, prepares, starts, pins, purges, repairs,
or deletes a model, and it never runs automatically.

From an exact model detail, the labeled preparation option is a second separate
default-no action. It is offered only for reviewed-seal serving profiles and
delegates to the fixed eight-stream SSH-over-RoCE preparation command documented
above for multi-rank profiles. The service revalidates exact identity, topology,
capacity, and all-rank completion; there is no fallback. The action does not
launch or qualify the model or assign a release status. Pin, purge, repair,
and durable-home removal remain direct CLI operations.

Schema-1/2 hot metadata is obsolete and cannot launch. If health marks an
instance repairable, inspect and remove only by its freshly issued ID:

```bash
scripts/model-library.sh hot legacy check <repair-id> --json
scripts/model-library.sh hot legacy remove <repair-id> --yes
# Pinned legacy state needs both explicit acknowledgements:
scripts/model-library.sh hot legacy remove <repair-id> --yes --force-unpin
```

Check and removal re-observe all confirmed ranks and managed containers. A
stale/ambiguous ID, current or malformed metadata, symlinked target, stopped or
running managed reference, or unreachable Docker/SSH fails closed. Removal
atomically retires one exact hot instance and does not follow embedded symlinks
or touch sibling instances/durable homes. If retirement is incomplete, rerun
health and use the rediscovered ID; never rename it over new content.

**Guarded durable-home removal:** inspect first; do not delete the cache path
manually. Prefer an exact `model_id@revision` query in destructive workflows:

```bash
# Read-only plan; ordinary blockers are reported and exit nonzero.
scripts/model-library.sh home check '<model_id>@<revision>' --json

# A single/last home needs an explicit availability-loss acknowledgement.
scripts/model-library.sh home check '<model_id>@<revision>' --allow-last-home

# Clear managed dependencies before removal.
scripts/down.sh <profile>
scripts/model-library.sh purge-hot <profile> --yes --force-unpin

# Re-check, then run the separate destructive command.
scripts/model-library.sh home check '<model_id>@<revision>' --allow-last-home
scripts/model-library.sh home remove '<model_id>@<revision>' --allow-last-home --yes
```

A normal blocked plan exits 1 and, with `--json`, prints the plan. Missing
topology, an unreachable confirmed node, unavailable Docker, or a contradictory
observation contract aborts planning because absence of references was not
proven. Legacy, unreadable, or malformed hot metadata remains a visible blocker
instead of being treated as absence. Every dependent managed hot state blocks,
not only `pinned`, and managed containers remain blockers even when stopped
because Docker can restart them later. The exact repository must contain only
the selected snapshot revision and no ref may point to another revision.

A recognized incomplete or refs-only Hugging Face hub occupancy — typically
`refs/` plus `refs/main` and empty hub metadata, with no complete
`snapshots/<commit>` payload — is inspectable and retireable through the same
`home check` then `home remove ... --yes` path. That occupancy is not a
complete durable home. The plan states the action (retire the incomplete
tree so the exact repository path becomes absent and a later source-attested
`home add` can proceed), the public model ID, the bound snapshot revision when
live `refs/main` names one 40-hex commit, the rank role, why it is eligible, what
exact hub directory will be deleted, and what will not be deleted (sibling
models, other revisions, hot trees, receipts history, running or unrelated
containers). Last occupancy of that identity still needs `--allow-last-home`.
A leftover incomplete occupancy is not last occupancy when a complete home of
the bound commit already exists; retiring that stub does not require
`--allow-last-home` or a primary-selection switch. Complete-home duplicate
removal still needs an explicit primary.
`home check` remains read-only. Without `--yes`, `home remove` changes
nothing. Catalog refresh never auto-deletes. An arbitrary non-empty unknown
tree, a multi-revision hub, a complete snapshot, a current-home attachment, or
an unbound `@unknown` row that live inspection cannot bind to one 40-hex commit
stays fail-closed. Complete homes keep the complete-home removal contract.

The removal command holds the exclusive lifecycle lock from observation through
deletion. Catalog refresh, primary mutation, and `home add` also take the
exclusive form; supported catalog reads, preparation, launch, readiness, and
leftover-fabric cleanup take the shared form. They therefore
cannot overwrite policy or create a new dependency between the check and
deletion. Execution repeats the
repository inspection, compares its metadata fingerprint, atomically retires
the exact repository, and refreshes the catalog. If recursive deletion fails
after retirement, stop and inspect the plan-bound `.pulsar-removing-*` path
reported by the command; do not download or manually rename content over it.

For duplicate cleanup, always pass `--node` for the non-primary home shown by
`cleanup-recommend`. The selected primary cannot be removed while another
complete home exists; select the intended survivor first. Before a primary
exists, no cleanup command is printed and a direct `home remove --node`
attempt is blocked. No duplicate is ever deleted automatically.

The guard covers Pulsar-managed containers and hot metadata. A manually created
container, process, bind mount, or open file outside those labels is not
discoverable by this contract and remains the operator's responsibility.

Hot trees live under `PULSAR_HOT_ROOT` (default `/var/tmp/pulsar-hot`), not as
durable N copies in every node’s HF cache. For a warm-home N-rank service the
accepted accounting is one durable home plus N−1 hot working copies. Hot purge
must never follow the home symlink target.

Inspect live admission on every confirmed rank before a large preparation:

```bash
scripts/model-library.sh budget
scripts/model-library.sh budget --json  # site-local automation; contains node/path data
```

The default preserves user-available filesystem space equal to
`max(64 GiB, 5% of total capacity)` on each selected rank and has no arbitrary
hard cap. A warm-home preparation charges zero new model bytes on the home rank
and the exact manifest size on each non-home rank; cold stage-only charges every
selected rank. Existing tracked, untracked, or malformed content below the hot
root is counted. Preparation, pin, and cold stage-only display the all-rank plan
and refuse before writes when any observation is missing or blocked.

`PULSAR_HOT_BUDGET_BYTES` adds an optional per-rank hard cap.
`PULSAR_HOT_RESERVE_BYTES` explicitly replaces the default reserve (including
`0` for controlled tests). These are policy overrides, not retry suggestions.
Pulsar never auto-evicts, silently relaxes capacity, or changes transport; purge
an unpinned hot instance or free disk, run `budget` again, and retry.
Ordinary stop retains unpinned prepared views; `PULSAR_HOT_STOP_POLICY=purge`
restores named-profile purge-on-stop for storage-first labs. Budget-based
eviction is not implemented.

**Current identity behavior:** any profile may reference a reviewed seal
under `models/seals/` with `EXPECTED_MODEL_SEAL="seals/<file>.json"`.
That seal must name a content-addressed document at
`models/validation-bundles/<validation_bundle_id>.json`. Profile load verifies
the bundle's model/seal projection, lab provenance/evidence, declared
external-artifact identities/digests, digest-pinned image, normalized
runtime/memory settings, and geometry against the live sourced profile.
Inspect the release binding with:

```bash
scripts/model-library.sh validation-bundle verify <profile>
scripts/model-library.sh validation-bundle verify <profile> --json
```

Catalog schema 2 selects only its immutable commit. Preparation full-hashes every
rank, compares model/commit/manifest to the expected seal, writes hot schema 3
with expected and observed provenance, and atomically creates that rank's
`<instance>/.pulsar/witness.json` before ready is published. A configured
mismatch always fails; validation status and the removed `--allow-unvalidated`
flag cannot bypass expected identity.

Launch rechecks the live profile/seal locally and the controller-provided
validation identity remotely **before** using the witness. An unchanged witness
checks only rank-local metadata and hashes zero model bytes. It covers the
canonical hub/snapshot targets, directory filesystem identity, exact revision
and logical file set, and each resolved file's
device/inode/size/`mtime_ns`/`ctime_ns`. The container mounts the hub read-only
and receives the exact `snapshots/<revision>` path rather than mutable
`main`. Labels and multi-node startup evidence include revision, identity
status, seal ID, and bundle ID; per-rank witness labels remain future work.

A missing, malformed, or drifted witness prints a message and runs a stable full
SHA-256 verification. Success atomically refreshes the witness and continues;
content mismatch or metadata changing during the full pass fails closed and
does not refresh. Prepare the model again if the fallback fails. Do not hand-edit
`hot.json` or `witness.json`, and do not treat a successful rehash of
`legacy-unsealed` content as lab validation.

The one-node diagnostic profile `qwen3-1.7b` is the first profile with a
reviewed seal and validation bundle; the flagship `deepseek-v4-flash` profile
is the second. Their `library-hot` preparation must match the reviewed identity, and home
acquisition enforces the same commit/manifest.
Profiles without a seal, including `qwen3-1.7b-2node`, may use `library-hot`
after full observed-content verification without a validation-status override.
Catalog
refresh enumerates complete `snapshots/<revision>` directories directly. A
sealed profile therefore finds its reviewed commit even when `refs/main` is
absent or has moved; only a legacy-unsealed selection consults an
unambiguous `refs/main`.

Sealed home acquisition downloads the exact commit and full-verifies every
copy before publication; profiles without a seal launch with
`identity=legacy-unsealed` after full verification. Follow
[models/seals/README.md](../models/seals/README.md) and
[models/validation-bundles/README.md](../models/validation-bundles/README.md)
for the lab issuance contract; never derive expected identity from a user
cache. If witness fallback fails, prepare the profile again; do not hand-edit
the witness.

**Upgrade note:** catalog schema 1 and hot schemas 1/2 are intentionally not
accepted for launch or trust. Health may recognize exact historical ownership
metadata only so the separate guarded removal workflow can retire it safely.
After upgrading, run `catalog refresh`, then
prepare each required profile again. Hot schema 3 instances created before witness
support remain readable: the first `library-hot` readiness check visibly
full-verifies and creates the missing rank-local witness. Current unsealed
profiles do not need a validation-status override.
Do not hand-edit or relabel old site-local state into the new schemas.

**Optional cold archive:** shared/local fill tier (conventionally
`MODELS_NFS=/mnt/Models`, overridable with `PULSAR_COLD_ROOT`; empty
`PULSAR_COLD_ROOT` disables cold). Layouts scanned:

- `Official Models/<org>/<name>/` (and `Community Models/…`) — flat trees
- `hub/models--org--name/` or `.cache/huggingface/hub/…` — HF hub trees

Resolve order is **warm complete home → cold (if configured) → fail closed**.
Cold is preferred over a fresh Hugging Face download when warm misses; it is
**not** the multi-node runtime filesystem. A sealed Hugging Face profile can
stage only from a source preserving the expected commit and complete manifest;
a flat archive with an inferred local revision will not match that seal.

```bash
# inventory
scripts/model-library.sh cold scan --json
scripts/model-library.sh cold show <org>/<name>
scripts/model-library.sh resolve <profile> --json   # warm, else cold

# grow federated library (durable warm home on this node’s HF cache)
scripts/model-library.sh cold adopt <org>/<name> --yes
scripts/model-library.sh catalog refresh

# or stage for this job only (cold remains sole durable copy)
scripts/model-library.sh cold stage-only <profile> --yes
scripts/up.sh <profile>
```

Unset/empty cold config skips the tier (no mount required). If cold is
configured but unreadable, flows that **need** cold (warm miss, absolute-path
conf, explicit `cold *`) fail closed; pure warm-catalog hits never require it.
See [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md) §3.

**One-shot NFS/RDMA preparation:** retired
([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)). The
`--backend fabric` prepare experiment, its `release-transfer` cleanup, and the
copy-vs-fabric `bench-prepare` B-gate were removed with the live-fabric
internals; preparation is `--backend copy` (ssh-control or ssh-roce) only.
Historical B-gate reports under `results/model-library/` remain valid
evidence.

**Prepare performance:** the accepted home-rank behavior is a validated
durable-home symlink/view with no second full write; non-home ranks
materialize in parallel. Wall-clock is often limited by materialization, not
raw RoCE line rate.

**SSH-over-RoCE policy for reviewed multi-rank preparation:** the same
copy-based preparation (rsync + SSH) targets topology **RoCE IPs** so bulk
TCP rides the fabric NIC without NFS/RDMA. It requires enrolled topology schema 2,
`sshd` reachable on fabric IPs, and routes via the confirmed RoCE netdev. The
transport IP is never a separate trust identity: strict checking always uses
the saved alias and enrolled key. The interactive reviewed-profile action is
fixed to eight streams with no fallback by ADR 0003. Low-level benchmark and
diagnostic commands may still select other transports/stream counts
explicitly.

```bash
# 1) Enroll exact control/RoCE identity while idle, then verify it
scripts/topology-ssh-trust.sh enroll
scripts/topology-ssh-trust.sh check

# 2) Prove the model-library RoCE map for the profile
scripts/model-library.sh probe-ssh-roce deepseek-v4-flash

# 3) A/B: control SSH copy vs SSH-over-RoCE copy (purges hot between).
#    Stay for the run; no sudo required for pure copy paths. Admission uses the
#    same all-rank filesystem reserve as normal preparation.
scripts/model-library.sh budget
scripts/model-library.sh bench-ssh-roce deepseek-v4-flash --yes \
  --tag "ssh-roce-$(date -u +%Y%m%dT%H%M%SZ)"

# Parallel bulk copy for large, many-blob models (experimental). Repeat both
# orders before comparing paths; 150 ms staggering avoids an sshd admission
# burst at 16 streams.
PULSAR_COPY_STREAM_STAGGER_MS=150 \
  scripts/model-library.sh bench-ssh-roce deepseek-v4-flash --yes \
    --copy-streams 16 --order roce-first --tag parallel-roce-first
PULSAR_COPY_STREAM_STAGGER_MS=150 \
  scripts/model-library.sh bench-ssh-roce deepseek-v4-flash --yes \
    --copy-streams 16 --order control-first --tag parallel-control-first

# Manual one-shot over RoCE TCP with the enrolled alias/key identity:
scripts/model-library.sh prepare <profile> --transport ssh-roce \
  --backend copy --copy-streams 8 --yes
```

`--copy-streams` accepts 1-16. Multi-rank `prepare` defaults to eight streams;
one-rank defaults to one. Above eight streams, the
connection stagger must be at least 100 ms (the default is 150 ms); the command
fails closed otherwise. Parallel copy currently supports a local endpoint on
one side of the transfer. A remote-home to remote-target relay fails explicitly
instead of silently reverting to one stream.

An initial two-run test produced a promising but highly variable 16-stream
result (46.42 s and 79.92 s), without proving physical source reads or
accounting for destination writeback. A later six-run alternating test
synchronized both filesystems and applied `POSIX_FADV_DONTNEED` before every
trial. Block counters then proved a full ~155.45 GiB source read each time:

- 8 streams: 75.60, 83.87, and 88.82 s (83.87 s median).
- 16 streams: 81.03, 85.09, and 88.61 s (85.09 s median).

The 1.4% median difference is below the observed run-order/storage variance, so
16 streams has no demonstrated full-model advantage over 8. After the first
trial, destination block-I/O busy time was 88-95% and source busy time was
84-93%; aggregate CPU was only 27-32%. Treat 8 streams as the current
experimental knee, and attribute the earlier 46-80 s spread mainly to
cache/writeback state rather than RoCE or SSH scaling. The small Qwen profile
also showed no benefit. Product defaults remain unchanged. See the
[alternating 8-vs-16 artifact](../results/model-library/deepseek-v4-flash-parallel-rsync-roce-8v16-alternating-20260810.json)
and the [earlier exploratory artifact](../results/model-library/deepseek-v4-flash-parallel-rsync-roce-16stream-20260810.json).

Report: `results/model-library/<profile>-ssh-roce-<tag>.json` with
`verdict` (`ssh_roce_faster` | `control_faster` | `tie` | `inconclusive`),
phase maps, and the RoCE IP map used. Use this before deciding whether to
rethink one-shot `nfs-rdma` versus `ssh-roce` (copy over RoCE TCP).

## Expected steady-state numbers (alert if far off)

Flagship under load (DSpark default-on): ~27 tok/s rollback/base /
**~43–48 tok/s** default single-stream on the **0731 benches** (no 20 GB
throughput re-run in `results/`); ~105 tok/s aggregate at c=8 base path; node temps
≤81–84 C, SM clock ≥2380 MHz. Memory
available fluctuates ±2 GiB with page cache — only a monotonic decline over
hours is a leak signal (none observed in 150-min soaks).

## Safety rails

- Spec decode is **not** "try random methods": only use paths that have
  validated `SPEC_DECODE_ARGS` and a positive ledger entry
  (docs/VALIDATION.md). The flagship defaults to DSpark; use
  `--no-spec-decode` as the operational rollback. Its k=5 is fixed by the
  checkpoint, not tunable. **Do not** enable ngram on GDN hybrids (corrupts
  output). Super MTP is opt-in; Laguna DFlash is marginal.
- Launcher-created containers carry stack ownership, profile, rank, topology,
  and physical-node labels (`io.pulsar.gb10.managed`, `.conf`, `.rank`,
  `.topology`, `.node-id`). Wizard and `down.sh` only stop
  services inventory marks `safe_to_stop`; `down.sh` revalidates before remove.
  Legacy unlabeled containers are refused. Any unobservable required node blocks
  automatic multi-node cleanup/replacement. See “Model switch (wizard)” above.
- Remote SSH uses BatchMode, a finite connect timeout/attempt count, and
  liveness bounds. Host values are passed after the SSH option terminator.
- Built-in API probes and Python validators honor `VLLM_API_KEY` / `API_KEY`.
  Dry-run command rendering redacts HF and API credentials. Prefer environment
  configuration over putting secrets in shell history.
- Wizard rollback after a failed switch uses only the exact launch contract
  captured immediately before stop, including effective flags, placement,
  storage source, and speculative-decode state. If that contract is incomplete,
  drifted, or cannot be retained, automatic replacement is refused before
  stop. A later direct `./pulsar start <profile>` uses current profile defaults
  and is a new manual launch, not exact rollback.
- Never run a GDN hybrid (qwen3.6-27b) cross-node.
- After any image change: clear `~/.cache/vllm` + Triton cache on every exact active rank, then run docs/REVALIDATE.md before calling it production.
