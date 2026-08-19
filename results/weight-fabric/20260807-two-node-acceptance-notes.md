# Two-node weight-fabric physical acceptance notes

> **Superseded / not promoted.**
> [ADR 0005](../../docs/decisions/0005-reject-live-nfs-rdma-serving.md)
> rejects live NFS/RDMA serving. This file is historical evidence. Do not
> rewrite the PASS/FAIL rows below. Do not promote from this file.

Date: 2026-08-07  
Repository base: `203dcd1`  
Profile: `qwen3-1.7b-2node`  
Initial scope: two serving nodes, one authoritative Hugging Face model copy

## Status

In progress. Fabric application and full integrity verification pass. The first
cold measurement is preserved as a failed traffic-proof artifact because it
exposed an instrumentation defect: RPC/RDMA payload increments the ConnectX HCA
port counters but bypasses Linux Ethernet netdevice byte counters. This record
is an operator narrative, not promotion evidence by itself. Machine-generated
benchmark and validation bundles are linked as they are produced. The feature
remains experimental. A later cold-launch attempt exposed a separate
root-squash access defect in the legacy full-cache export. The model-repository
subtree redesign is now physically applied and passes export, mount, container,
and launch-dry-run validation. Its first measured cold launch reached health in
126.462 seconds and passed the smoke/API path, but the stock CUDA-graph runtime
later reproduced the documented cross-node sampling hang during capture.
Storage remained healthy after clean service teardown. The controlled eager
retry, permanent profile default, strict captures, c=8 sweep, 30k context gate,
one clean restart, and final-profile cold launch now pass. Replicated-serving
comparison now also passes with exact output parity and equivalent resident
inference throughput. Interrupted-start, link-loss, and NFS-service restart
recovery now pass. Three-node concurrent loading, owner fault recovery,
restart-loop/soak, and final closeout evidence remain required. The first
physical link-loss attempt restored the selected rail and
storage cleanly but exposed that its benchmark SSH control session had resolved
onto the faulted data rail. The corrected topology-pinned control-path rerun,
post-fault relaunch, and correctness gates now pass.
Three subsequent NFS-restart driver attempts stopped safely before fault
injection because the owner mounts `/run` with `noexec` and the transient unit
tried to execute its staged script directly. The corrected private driver now
invokes that bounded script through `/bin/bash` and passes its non-mutating
hardware preflight. The subsequent bounded outage, storage recovery, service
relaunch, and correctness gates now pass.

## Privacy and evidence rules

- Do not record node hostnames, SSH targets, IP addresses, user home paths,
  access tokens, or raw topology node IDs in this publishable file.
- Refer to machines only by topology rank or deterministic fingerprints emitted
  by the artifact tooling.
- Preserve failures and partial results; do not rewrite them as passes.
- Record every physical or privileged action and the corresponding recovery.
- Generated public bundles must pass their artifact privacy audit before they
  are treated as shareable evidence.

## Objectives

1. Prove that the client reads the sealed model snapshot over the configured
   RoCE interface using NFSv4.2/RDMA, without TCP or control-LAN fallback.
2. Compare cold replicated-local and cold fabric reads using the same model
   revision and exact serving ranks.
3. Launch the tested two-node canary from fabric storage and run correctness,
   determinism, recovery, and lifecycle checks.
4. Verify that no complete durable model replica remains on a client at close.

## Running log

| UTC time | Phase | Action | Result / insight |
|---|---|---|---|
| 2026-08-07 | Preparation | Reviewed `WEIGHT_FABRIC.md` and `REVALIDATE.md`; created an isolated validation branch. | The repository distinguishes control-plane support from storage promotion. Synthetic self-tests are insufficient; physical traffic, correctness, fault, recovery, and soak artifacts are required. |
| 2026-08-07T20:50Z | Discovery | Ran live, read-only fabric discovery and compared it with the confirmed manifest. | Three GB10 systems form a verified RoCE full mesh with two usable rails per pair. The topology identity matches the saved fabric configuration. One mDNS/SSH candidate was rejected as a duplicate machine identity, which is expected alias de-duplication rather than a missing node. |
| 2026-08-07T20:50Z | Hardware preflight | Ran `scripts/doctor.sh` and cluster inventory. | GPU, Docker, SSH, and the cluster network passed on all three ranks. A managed two-rank DeepSeek service is active on ranks 0 and 1, leaving about 6 GiB available on each. Cold-cache eviction and storage changes must not proceed until that service is deliberately stopped. |
| 2026-08-07T20:50Z | Existing configuration | Validated the site-local `qwen3-1.7b-2node` configuration and checked replicated cache placement. | The config selects rank 1 as owner and all three ranks as storage readers. This is the evidence-based owner choice because rank 1 already has the complete sealed Qwen snapshot; serving rank 0 does not. |
| 2026-08-07T20:50Z | Fail-closed check | Ran the full fabric readiness check before applying any system state. | Both client routes are correct and both clients lack a complete replica, but their NFS/RDMA mounts are absent and the owner service is not ready. The check correctly reports `owner-unready` instead of falling back to TCP, the control LAN, or replicated storage. |
| 2026-08-07T20:50Z | Prerequisites | Ran the read-only prerequisite check on all configured ranks. | Python, package tooling, NFS client/server tools, `xprtrdma`, `nfsd`, `svcrdma`, and the owner Hugging Face CLI are present. No package installation is required. Unattended mode is blocked only because every node requires an attended sudo password; Pulsar correctly refuses to change sudoers or bypass that policy. |
| 2026-08-07T21:37Z | Maintenance start | With operator approval, stopped the tracked `deepseek-v4-flash` service through `scripts/down.sh`. | The ownership-aware stop removed only the exact managed container from each of its two ranks and verified both were absent. Follow-up inventory found no managed or unmanaged GPU services. Available memory recovered from roughly 6 GiB to 117.85–118.23 GiB across all three ranks. The topology is now suitable for controlled cold-cache work. |
| 2026-08-07T23:21Z | Fabric application | The operator ran `scripts/weight-fabric.sh apply qwen3-1.7b-2node --interactive-sudo` from rank 0 and authenticated locally and over the scripted SSH sessions. | Configuration `268c169d4ed1` installed one read-only owner export and mounted both exact RoCE clients. The command's closing single-copy check reported all three ranks ready over NFSv4.2/RDMA. No repository checkout was required on a remote rank because Pulsar streamed the privileged scripts over SSH. |
| 2026-08-07T23:21Z | Independent verification | Reran the full rank-scoped check and `verify --json` from the controller. | Owner readiness, configured routes, exact RDMA mounts, full sealed-manifest checksums, and client replica absence all passed on ranks 0–2. The full verification completed successfully, but it was not a cold or throughput-comparable measurement and must not be reported as performance evidence. |
| 2026-08-07T23:23Z | Privilege recheck | Repeated unattended prerequisite validation from the automation session after the successful attended apply. | Every rank still reports `password-required`. Sudo authentication is scoped to the operator's terminal/remote TTY and is not reusable by a separate automation session. Cold-cache benchmarks therefore require the operator to launch the documented `--interactive-sudo` command; Pulsar and the validation agent must not transport a password or weaken sudoers to automate it. |
| 2026-08-07T23:26Z | First cold fabric benchmark | Ran the two-serving-rank cold fabric benchmark and preserved `results/weight-fabric/qwen17b-fabric-2node-20260807/`. | Both ranks successfully read the exact 4,079,450,110-byte snapshot. Maximum rank time was 3.35 seconds; aggregate logical throughput was 2.27 GiB/s. Control-LAN traffic stayed negligible and the public artifact privacy audit passed. The command failed its traffic proof because the selected fabric netdevice counters observed only 18,995 bytes rather than model-sized payload. The failed directory was retained and the overwrite guard correctly rejected reuse of its tag. |
| 2026-08-07T23:34Z | Counter diagnosis | Inspected the live NFS mount and ran one controlled direct read of the largest 3,441,185,608-byte shard while sampling both HCA-port and Ethernet-netdevice counters. | The mount is NFSv4.2 with `proto=rdma`; NFS mount statistics record RDMA transport and model-sized READ replies. The direct read increased client HCA RX and owner HCA TX by exactly 3,637,901,164 bytes each, while the corresponding Ethernet netdevice counters increased by only 6,147 and 11,606 bytes. RPC/RDMA data bypasses these netdevice byte counters on this ConnectX configuration. The benchmark must use HCA port counters (whose data units are four octets) or an equally specific RPC/RDMA counter before traffic proof can pass legitimately. |
| 2026-08-07T23:46Z | Instrumentation correction | Replaced positive fabric proof with exact configured HCA `port_rcv_data`/`port_xmit_data` sampling, converted four-octet units to bytes, retained netdevice byte counters for the control-LAN bound, and published counter source/HCA provenance in benchmark JSON. | Seven-field counter snapshots now bind rank, node fingerprint, role, paired netdevice, and exact HCA. The report rejects a mismatched HCA. Bash/Python syntax, whitespace checks, the focused weight-fabric suite, and the full repository `scripts/selftest.sh` all pass. The original failed artifact remains unchanged; the corrected physical run must use a new tag. |
| 2026-08-07T23:58Z | Corrected cold fabric benchmark | Ran the corrected two-serving-rank benchmark and preserved `results/weight-fabric/qwen17b-fabric-2node-hca-20260807/`. | **PASS.** Both ranks read 4,079,450,110 bytes. Maximum rank time was 3.60 seconds and aggregate logical throughput was 2.11 GiB/s. Client HCA RX and owner HCA TX matched exactly at 4,312,686,040 bytes, about 5.7% above the logical payload; control-LAN traffic was only 216,489 bytes. All traffic checks passed, rank stderr files were empty, and the public artifact privacy audit passed every check. |
| 2026-08-08T00:01Z | Replicated baseline staging | Used the normal replicated staging path to create a temporary complete Qwen cache on serving rank 0 and confirm the same revision on rank 1. | Both serving-rank replicated checks pass and the owner's sealed fabric snapshot still passes full integrity. Single-copy readiness now deliberately reports `replica-present` for rank 0; this is the expected fail-closed state during the A/B baseline and must be cleared with the guarded replica purge before fabric launch validation. |
| 2026-08-08T00:53Z | Replicated sanitizer defect and correction | The first replicated benchmark attempt stopped before sudo, cache eviction, counter capture, or artifact publication with `TypeError: 'int' object is not iterable`. | Real replicated `check-weights` JSON uses `nodes` as the integer world size and `ranks` as the record list; the sanitizer incorrectly assumed both fields were lists because fabric-check JSON uses `nodes` as a list. The sanitizer is now type-aware, preserves the numeric count, and scrubs only list-valued rank/node records. A new end-to-end cold replicated shell benchmark fixture uses the real numeric-count shape, includes private rank fields to prove sanitization, passes the artifact audit, and cleans its temporary replica before later single-copy lifecycle tests. Focused syntax, whitespace, and weight-fabric tests pass. |
| 2026-08-08T01:29Z | Independent Grok 4.5 high review | Ran a user-authorized, read-only external review of the working-tree code, tests, documentation, failed artifact, corrected artifact, and troubleshooting process. | **READY FOR REPLICATED PHYSICAL RERUN.** The reviewer found no blocking correctness defect in the HCA sampler or sanitizer fix and agreed that preserving failures, using a new tag, and pausing before reruns were correct. It identified pre-promotion hardening: require client-RX/owner-TX symmetry and a fabric upper bound, resolve rather than assume HCA port 1, verify the live netdev-to-HCA binding, document fail-closed counter reset/wrap behavior, and add an exact production-shape sanitizer unit case. Local source inspection confirmed the first three gaps are present. |
| 2026-08-08T01:47Z | Cold replicated A/B baseline | Ran the corrected two-serving-rank local-replicated benchmark and preserved `results/weight-fabric/qwen17b-replicated-2node-hca-20260807/`. | **PASS.** Both ranks read 4,079,450,110 bytes from complete local replicas. Maximum rank time was 1.57 seconds and aggregate logical throughput was 4.84 GiB/s. Fabric HCA traffic was only 456 bytes, control-LAN traffic was 45,521 bytes, both stderr files were empty, both replicated integrity records passed, and the public artifact privacy audit passed. Against the comparable cold NFS/RDMA result at 2.11 GiB/s, fabric delivered about 43.6% of replicated throughput (2.29× longer maximum-rank time). This is an I/O A/B result, not a serving startup or inference comparison. A post-run check reconfirmed both local replicas and the expected fail-closed `replica-present` fabric state on rank 0. |
| 2026-08-08T01:48Z | Guarded replica purge and restoration | Ran the profile-scoped client-replica purge after preserving the replicated baseline, then repeated the full single-copy check and JSON verification. | The temporary Qwen cache was removed only from configured client roles; the owner was excluded. Full verification passed on all three storage ranks with exact routes, NFSv4.2/RDMA mounts, sealed-manifest integrity, and durable client-replica absence. The single-copy state is restored and ready for fabric serving validation. |
| 2026-08-08T02:02Z | First cold-start attempt and wrapper correction | Dropped serving-rank page caches successfully, then invoked the documented `scripts/up.sh ... --weight-source fabric --skip-warmup` command. | No container started because `scripts/up.sh` rejected `--skip-warmup`, although the runbook documented it and `cluster/start-cluster.sh` already implemented it. The public wrapper now parses, documents, and forwards the flag. A wrapper-boundary regression was added; Bash syntax, CLI-input tests, topology tests, whitespace checks, and the full repository self-test pass. The failed attempt produced no startup metric and must not be reported as startup evidence. |
| 2026-08-08T02:02Z | Launch prerequisite repair | The corrected physical dry-run then found the exact `vllm/vllm-openai:v0.26.0` image absent on serving rank 1. | Streamed the existing image from rank 0 to rank 1 with the profile-scoped image synchronizer. Exact image presence now passes on both serving ranks, and the complete fabric `up.sh --dry-run --skip-warmup` path passes weights, memory, topology, and forwarding checks without starting a container. Because image import can alter filesystem cache state, the privileged page-cache drop must be repeated before the measured cold launch. |
| 2026-08-08T02:46Z | Cold-launch access diagnosis | Repeated the cold-start preparation and attempted the fabric launch after the wrapper and image repairs. The tracked launch cleaned up both rank containers after Hugging Face offline resolution could not see the snapshot through the client mount; no startup metric was published. | The schema-1 NFS export exposed the complete Hugging Face home with default `root_squash`. Its `0700`/`0600` model content was owned by the non-root cache user, while container root was mapped to anonymous UID/GID 65534. A disposable network-disabled container reproduced `ref_exists=False` and `ref_readable=False`. The active export table confirmed the anonymous mapping. This is an export-identity defect, not a missing model or network fallback. |
| 2026-08-08T02:46Z | Model-repository subtree redesign | Reworked configuration schema 2, application checks, launcher mounts, tests, and the operator guide so fabric mode exports and bind-mounts only the selected `hub/models--<publisher>--<model>` repository. | The generated ACL remains read-only and exact-RoCE-address scoped, keeps `root_squash`, maps the squashed identity to the repository's verified non-root owner, and verifies the active export table. Tokens, unrelated repositories, and the Hugging Face home remain outside NFS and the fabric-mode container mount. Schema-1 full-cache configurations are teardown-only, and replacement fails while an export file, active kernel export, or client mount may remain. Bash/Python syntax, whitespace checks, the focused weight-fabric suite, and the full `scripts/selftest.sh` pass. Physical migration and a container readability probe remain pending. |
| 2026-08-08T03:10Z | Independent model-subtree review | Ran the previously authorized Grok 4.5 High CLI in read-only mode against the tracked redesign diff, excluding site-local configuration and private artifacts. | The reviewer found no P0 and agreed that schema-2 path math, exact Docker model mount, root-squash mapping, legacy launch rejection, and the documented teardown-first migration are coherent. It made physical migration conditional on closing three P1 gaps: reject orphaned broader exports, prove nested repository readability for the mapped identity, and roll back partial apply state. |
| 2026-08-08T03:10Z | Review hardening and regression closure | Added owner-wide Pulsar export-file and active-parent-export rejection, a streamed permission/symlink walk for the exact mapped UID/GID, and an exit-trapped best-effort rollback after the first apply mutation. | Negative fixtures now cover an unreadable nested file, root-owned repository, stale Pulsar export, broader active export, drifted anonymous mapping, mid-loop client mount failure, sibling model/token exclusion, and installed schema-1 teardown before schema-2 replacement. Narrow CLI output, Bash/Python syntax, `git diff --check`, focused weight-fabric tests, and the full repository `scripts/selftest.sh` pass. These are control-plane results; physical migration remains pending. |
| 2026-08-08T03:13Z | Independent closure review | Asked Grok 4.5 High to inspect only the hardened tracked hunks and re-evaluate its three P1 findings. | **No remaining P0/P1 blocker.** The reviewer marked orphaned/broader export rejection, complete mapped-identity readability, and partial-apply rollback closed, and judged the documented physical teardown-to-schema-2 migration safe to attempt. It noted only non-blocking limits: the access model intentionally uses mode bits rather than ACL/supplementary-group evaluation, and symlinked directories are validated but not recursively traversed because the sealed Hugging Face layout uses file links. |
| 2026-08-08T03:29Z | Physical schema migration, stage 1 | The operator completed attended schema-1 teardown from the controller. Inventory then confirmed zero services and zero unmanaged GPU processes. Replaced the preserved site configuration with schema 2 while retaining the same confirmed owner rank, three-node storage scope, cache/mount roots, NFS/RDMA port, and rail index. | Replacement guard passed, proving the legacy export file, active kernel export, and both client mounts were absent before overwrite. Configuration `28822c68f5e8` reports `export_scope=model-repository` and an export path distinct from the Hugging Face home. The real owner tree passed mapped-identity access for 6 directories, 14 files, and 12 in-repository links. Sealed manifest `5094934e106d` covers 12 logical files and 3.80 GiB inside the selected repository. Apply and physical client/container proof remain pending. |
| 2026-08-08T03:40Z | Physical schema migration, stage 2 | The operator applied schema 2 with attended sudo. Reran full readiness and checksum verification, then independently inspected the generated export, active kernel export table, both client mounts, disposable serving-image containers, and the launcher dry run. | **PASS.** All three storage ranks are ready under configuration `28822c68f5e8`. The owner exposes exactly the selected model repository through two read-only, root-squashed mapped-identity grants; no stale Pulsar export or broader active parent export remains. Each client has exactly one nested read-only NFSv4.2/RDMA mount and no durable model replica. Network-disabled disposable containers on both serving ranks read the sealed snapshot through the exact production bind target, see no sibling repository or token path, and were removed automatically. The full cluster preflight and rendered per-rank launch commands pass and expose only the selected repository; no serving container was started. Cold cache eviction and the measured launch remain pending. |
| 2026-08-08T03:54Z | First schema-2 cold serving launch | The operator dropped page caches on the two idle serving ranks and launched the two-rank canary from the model-repository fabric. Preserved `qwen17b-fabric-subtree-startup-cold-20260808.json`, checked live service inventory and API health, and inspected each running container's labels and mount metadata. | **PASS.** Configuration `28822c68f5e8` reached first health in 126.462 seconds. Both managed ranks remain healthy, the third rank remains unused, and no unmanaged GPU or stale workload was found. The post-health completion and a second status smoke both traversed the distributed API. Each live container has exactly one Hugging Face bind, at the selected model repository target, read-only, with matching schema-2 fabric labels. The public bundle audit scanned 40 UTF-8 files (89,057 bytes) and found no private site values, private JSON fields, staging files, or symlinks. This establishes cold startup and API-path viability, not determinism or model correctness. |
| 2026-08-08T04:05Z | Stock graph-mode capture failure and clean recovery | Started the strict 30-prompt greedy gate against the still-running cold-launch service. Capture A completed multiple sequential requests, then the API became unhealthy while both containers still reported `Up`. Preserved the engine-side diagnosis in this operator record, stopped only the exact managed profile, and repeated idle inventory plus full fabric integrity checks. | **FAIL — runtime stability, not storage.** The engine reported repeated shared-memory broadcast starvation followed by `RPC call to sample_tokens timed out`, `EngineDeadError`, and an HTTP 500. This matches the repository's documented stock-v0.26.0 cross-node CUDA-graph signature. The service had already loaded all weights, reached health, and answered smoke requests; after teardown, all three nodes were idle and configuration `28822c68f5e8` again passed routes, exact mounts, checksums, and client-replica absence. The gate runner also discarded capture stderr and exited before a verdict artifact because of `set -e`; it now preserves per-capture stderr, prints its tail, and stops explicitly. A unique eager-mode retry is required before changing the profile. |
| 2026-08-08T04:19Z | Controlled eager-mode validation | Relaunched the same fabric configuration with `--enforce-eager` as a temporary runtime override, proved both ranks had disabled torch compilation and CUDA graphs while retaining FLASH_ATTN, and ran the full live gate sequence under a unique tag. Added structured needle output after noticing that the original passing context result existed only in console output, then repeated it into the evidence set. | **PASS.** Warm-cache launch-to-health was 94.566 seconds and is not compared directly with the cold graph-mode result. Captures A and B were identical for all 30 prompts with zero logprob delta. The standard c=1/2/4/8 sweep passed; c=8 served 16 requests at 608.57 aggregate tok/s. The 30k context gate passed 3/3 at 29,097 measured prompt tokens. The API remained healthy and the fatal sampling signature did not recur. Capture stderr logs, both captures, bench JSON, structured needle JSON, and the separate warm startup metric are preserved. |
| 2026-08-08T04:19Z | Permanent canary fix and restart proof | Added the physically proven eager workaround to the exact Qwen two-node diagnostic profile, documented the stock-graph failure and passing evidence, stopped the temporary service cleanly, and relaunched without a runtime override. Compared a new strict capture pair with the prior-boot eager capture and repeated the concurrency sweep. | **PASS.** The profile-default restart reached health in 94.657 seconds. Its two new captures were 30/30 identical to each other and 30/30 identical to the prior boot, again with zero logprob delta. The repeated c=8 result was 608.58 aggregate tok/s. Bash/Python checks, focused validation tests, `git diff --check`, and the complete `scripts/selftest.sh` pass. This proves same-configuration restart determinism; a replicated-serving or independent reference remains required before claiming storage-independent model correctness. |
| 2026-08-08T09:29Z | Final-profile cold eager launch | The operator cleanly stopped the managed canary, dropped page caches on both idle serving ranks with attended sudo, and launched from schema-2 fabric storage using the permanent profile without a runtime override. Preserved `qwen17b-fabric-subtree-startup-cold-eager-profile-20260808.json` and rechecked managed rank state, API health, smoke inference, effective container arguments, and fatal engine signatures. | **PASS.** Configuration `28822c68f5e8` reached first health from cold caches in 105.731 seconds. Both ranks prove `--enforce-eager` came from the profile, the service remained healthy for roughly one hour, smoke inference still succeeds, the unused third rank remains empty, and neither rank log contains the prior `sample_tokens` timeout or engine-death signature. This is the comparable cold timing for the final canary configuration; the earlier 126.462-second graph-mode cold result remains historical failure-path evidence. |
| 2026-08-08T15:21Z | Replicated-versus-fabric serving A/B | Stopped the fabric canary, staged temporary local model copies on the two serving ranks, confirmed that single-copy readiness failed closed with `replica-present`, then launched the unchanged eager profile with replicated storage. Preserved `qwen17b-replicated-serving-startup-warm-eager-20260808.json` and the `qwen3-1.7b-2node-replicated-serving-ab-20260808-*` strict capture/throughput bundle, stopped the service, purged only client-role copies, and repeated full fabric readiness and verification. | **PASS.** Replicated first health was 94.761 seconds. Live labels, arguments, and mounts proved local replicated storage with no fabric model bind. Replicated captures were 30/30 identical within the run and 30/30 identical to the preserved fabric capture, with zero logprob delta. Resident c=8 throughput was 607.37 aggregate tok/s versus 608.57 for fabric (about -0.20%); this parity does not erase the separately measured cold-I/O difference. Cleanup removed the temporary client copies while preserving the owner, all three storage ranks returned to full single-copy readiness, and final inventory found no managed, unmanaged, or stale service. |
| 2026-08-08T15:38Z | Interrupted-start preflight hardening | Before injecting the physical interrupt, inspected the launcher's documented cleanup path and found that explicit startup errors called immutable-ID cleanup but `INT`, `TERM`, and `HUP` had no trap. No live service was started in that state. Added a signal handler that disables repeat traps, calls the same launch-scoped cleanup, and returns the conventional signal exit code. | The change passes Bash syntax, `git diff --check`, and all 134 focused lifecycle/ownership tests. Static guards require all three signals to reach immutable launch cleanup. This was a real safety prerequisite: without it, Ctrl-C during loading would have orphaned both containers and contradicted the fault matrix. |
| 2026-08-08T15:38Z | Physical interrupted start and recovery | Started the warm-cache fabric canary, waited until both launch-created immutable IDs were recorded and the launcher entered its pre-health wait, then sent `SIGINT`. Preserved the raw launcher transcript and pre/post inventory JSON in ignored private staging; no private site values were added to the public evidence tree. Reverified the complete storage fabric, relaunched, ran strict gates against the pre-fault baseline, then stopped through the ownership-aware lifecycle path. | **PASS.** The interrupted command exited 130, reported both tracked-ID removals, and completed cleanup in under one second. It emitted neither `/health`/`READY` nor the requested startup-success metric. Immediate inventory was idle with no managed, unmanaged, or stale workload, and all three storage ranks passed routes, exact NFSv4.2/RDMA mounts, manifest integrity, and client-replica absence. Recovery reached health in 94.392 seconds; captures were 30/30 identical within the recovered boot and 30/30 identical to the pre-fault baseline with zero logprob delta. The c=8 recovery result was 608.96 aggregate tok/s. Final stop, inventory, and full readiness checks pass. |
| 2026-08-08T15:55Z | First physical RoCE link-loss attempt | From the idle controller, started the two-rank paced cold read, dropped the selected serving-client RoCE rail for a bounded 10-second window, and restored it from the guarded fault driver. The remote rank process then reported an SSH timeout and the benchmark exited nonzero. | **FAIL — management-path proof, not storage recovery.** The saved SSH alias resolved to the owner's RoCE address, so the rank-control session crossed the exact rail being faulted despite the confirmed topology recording a separate control address. After restoration, the selected link was carrier-up with its configured route, idle inventory was clean, and all three storage ranks passed full route, mount, checksum, and replica-absence verification. The benchmark's exit cleanup also erased its public staging directory, leaving only the ignored private transcript/event log; this attempt is not promotion evidence. Confirmed-node SSH now preserves the saved alias for user/host-key identity while pinning transport to the manifest control address. Rank streams remain private until exact-value redaction, and a rank failure now publishes an audited `failure.json` bundle. The focused weight-fabric suite passes, and the corrected private driver passes a non-mutating independent-control-route preflight under a new tag. |
| 2026-08-08T16:28Z | Corrected physical RoCE link-loss recovery | Repeated the two-serving-rank paced cold read under a unique tag after proving the owner control route used the independent confirmed interface. The selected client rail went down 2.320 seconds after that client's read began, remained down for 10.518 seconds, and was restored 47.951 seconds before the client completed its sealed-snapshot read. Preserved `results/weight-fabric/qwen17b-fabric-link-loss-recovery-control-path-20260808/`, then ran full storage verification, a post-fault warm relaunch, strict captures against the pre-fault baseline, the concurrency sweep, ownership-safe stop, and final idle/readiness checks. | **PASS.** Both ranks read the exact 4,079,450,110-byte snapshot and emitted no benchmark stderr. The client HCA RX and owner HCA TX were symmetric at 4,312,688,692 bytes; control-LAN traffic was 1,492,927 bytes and every traffic proof passed. The 64 MiB/s pacing cap produced a 60.789-second maximum rank time, which is intentionally not throughput-comparable and does not measure recovery latency. The public audit passed all checks across 11 files. All three storage ranks passed route, exact NFSv4.2/RDMA mount, checksum, and client-replica absence after the fault. Recovery launch reached health in 105.565 seconds and passed smoke; its new captures were 30/30 identical within the boot and 30/30 identical to the pre-fault capture with zero logprob delta, while c=8 reached 608.91 aggregate tok/s. Final teardown left no managed, unmanaged, or stale workload, and full fabric readiness still passes. |
| 2026-08-08T16:56Z | NFS-restart harness arming diagnosis | Attempted to arm the bounded, self-restoring owner-side NFS outage three times. Each transient unit was rejected before its trigger because the owner runtime filesystem is mounted `noexec` and `systemd-run` was given the staged `/run` script as its executable. | **NO FAULT INJECTED.** NFS never stopped, no page caches were dropped, no benchmark began, and no result or event artifact was produced. Cleanup removed the bounded remote script, directory, and unit state after each attempt. Independent checks found NFS active, RDMA port 20049 present, all three storage ranks fully verified, and idle inventory. The private driver now starts `/bin/bash` with the staged script as its argument, preserving the same timeout, cancellation markers, and guaranteed service-restore trap. Bash syntax, generated-command syntax, clean-namespace checks, the non-mutating physical preflight, and `git diff --check` pass. The unchanged result tag is safe to reuse for the first actual fault. |
| 2026-08-08T17:08Z | Physical NFS-service restart recovery | Ran a paced two-serving-rank cold read from idle state and triggered the armed owner-side transient unit after the client read began. NFS was stopped for 10.046 seconds, restarted, and confirmed on RDMA port 20049 before the unit completed. Preserved `results/weight-fabric/qwen17b-fabric-nfs-restart-recovery-20260808/`, private timestamped fault events, `qwen17b-fabric-nfs-restart-relaunch-warm-20260808.json`, and the `qwen3-1.7b-2node-fabric-nfs-restart-recovery-20260808-*` recovery gates. | **PASS.** The remote client started 2.646 seconds before NFS stopped, resumed after restoration, and completed the exact 4,079,450,110-byte snapshot; the concurrent owner-local rank read completed the same snapshot. Client HCA RX and owner HCA TX matched at 4,313,411,236 bytes, control-LAN traffic was 2,250,707 bytes, both rank stderr files were empty, and the 11-file public audit passed. The 64 MiB/s cap and 64.551-second maximum rank duration are fault pacing, not throughput or recovery-latency evidence. NFS/RDMA, transient-unit cleanup, all three storage ranks, and idle inventory passed before relaunch. Recovery reached health in 105.711 seconds and passed smoke; captures were 30/30 identical within the boot and 30/30 identical to the pre-fault baseline with zero logprob delta, while c=8 reached 607.85 aggregate tok/s. No fatal runtime signature appeared. Exact teardown returned the cluster to idle, and all three ranks again passed full single-copy readiness. |
| 2026-08-08T20:50Z | Physical owner reboot, storage recovery, and attended boot completion | From idle state, started a paced cold read on both serving ranks and triggered the armed owner reboot only after the independent client read began. Preserved the expected owner-local rank failure, the completed client read, old/new boot fingerprints, observed offline interval, and the audited public bundle at `results/weight-fabric/qwen17b-fabric-owner-reboot-storage-recovery-20260808/`. The first monitor incorrectly conjoined storage readiness with `systemctl is-system-running`; Docker, NFS/RDMA, the exact export, and the client read recovered, but full boot remained `starting` on `plymouth-quit-wait.service`. After read-only diagnosis, the operator explicitly started the standard `plymouth-quit.service`, then reverified the same boot, normal targets, zero failed units, all three storage ranks, and idle state. | **FAIL — automatic full-boot recovery; PASS — storage and post-intervention serving.** The owner went offline 0.947 seconds after the reboot command, remained observably offline for 36.284 seconds, and returned to SSH after 37.231 seconds. The independent client began 2.330 seconds before reboot and completed the exact 4,079,450,110-byte snapshot 25.743 seconds after SSH returned. NFS/RDMA recovery is bounded above at 62.974 seconds; the first monitor did not capture its exact timestamp, so none is inferred. Manual Plymouth completion took 13.763 seconds and was required before systemd reached `running`; the eight-file public privacy audit passed. After intervention, a fabric relaunch reached health in 106.508 seconds, strict captures were 30/30 identical within the boot and 30/30 identical to the pre-reboot baseline with zero logprob delta, and c=8 reached 615.03 aggregate tok/s. Exact teardown, idle inventory, full three-rank integrity, and client-replica absence pass. The owner-reboot promotion box remains unchecked pending a clean automatic retry after an approved boot-completion remediation. |
| 2026-08-08T22:10Z | Independent Grok 4.5 High owner-reboot review | Sent the audited recovery evidence, relevant documentation, recurrence observations, and the proposed headless remediation to the authenticated Grok CLI for a read-only systems review. The reviewer inspected the requested local files; its attempted upstream fetch was denied by the non-interactive read-only policy, so its final report makes no web-source claim. No repository or host state was changed by the reviewer. | **AGREES WITH THE SPLIT VERDICT; GUI-PRESERVING OPTIONS REQUIRE MORE EVIDENCE.** Grok judged the most likely mechanism to be a failed GDM-to-Plymouth quit handoff, with medium-high confidence, but distinguished that mechanism from the still-unsealed node-local trigger. It kept `multi-user.target` as the lowest-entropy promotion path, while identifying two reversible GUI-preserving trials: remove the `splash` kernel option while retaining `graphical.target`, or—only after more evidence—a non-conflicting oneshot that directly runs `plymouth quit` after GDM is active and Plymouth is still present. It rejected masking `plymouth-quit-wait.service` as hiding the incomplete handoff and rejected ad hoc edits to GDM's deliberate `After=`/`Conflicts=` graph. Before choosing a fix, it recommends comparing affected and unaffected ranks' unit text/overrides, boot journals, package versions, kernel command lines, Plymouth daemon state, and DRM/seat state. Any GUI-preserving candidate must pass bounded automatic boot checks and repeated clean reboots before the physical owner-reboot gate is retried. |
| 2026-08-08T22:22Z | Read-only three-rank boot forensics | Ran the ignored, no-sudo collector against all confirmed ranks over their verified control paths. Captured exact unit text/overrides, package and kernel inputs, current/previous boot journals, Plymouth/GDM runtime, and DRM/seat state. All collectors exited zero; raw site-specific files remain private with recorded SHA-256 values. Published the sanitized, audited result at `results/weight-fabric/qwen17b-owner-reboot-boot-forensics-20260808/`. | **DIAGNOSED — DISPLAY PRESENCE IS THE ONLY MATERIAL RANK SPLIT FOUND; CAUSALITY STILL NEEDS A CONTROLLED REBOOT.** Packages, semantic kernel options, GRUB/GDM configuration, on-disk unit fragments, and relevant drop-ins match. Both affected serving ranks booted without simpledrm or a connected output; Xorg used an NVIDIA `NULL` MetaMode, GNOME Shell never registered its GDM session, and Plymouth wait persisted for 17 hours or 30 minutes on the current boots and across prior boots. The unaffected rank retained a connected simpledrm framebuffer, registered its GDM session, and completed Plymouth wait automatically in 3.314 seconds on the current boot and 5.764 seconds on the previous boot. The attended `plymouth-quit.service` transaction completed systemd targets by conflicting with and stopping GDM; current evidence shows no display manager or graphical session on either affected rank afterward. This corrects the earlier interpretation: storage and serving recovered after intervention, but graphical boot did not. The most evidence-aligned GUI-preserving next test is a connected display at owner boot; removing `splash` remains a bypass candidate, not proof of a healthy GUI. |
| 2026-08-08T23:15Z | Controlled owner boot with a real display | With explicit operator approval, connected and powered a real display on owner rank 1, confirmed the cluster was idle, rebooted only that rank, and observed a changed boot identity over the independent control path. No boot configuration, service policy, workload, or fault injection was changed. Preserved a new three-rank private capture with verified hashes and published the sanitized result at `results/weight-fabric/qwen17b-owner-display-connected-boot-20260808/`. | **PASS ONCE — DISPLAY-TRIGGER HYPOTHESIS STRONGLY SUPPORTED; PROMOTION STILL PENDING.** The owner was observably offline and returned over SSH under a new boot identity. One DRM output was connected, GDM registered its greeter on `seat0`, and Plymouth wait completed automatically with `Result=success` in 3.218 seconds. Systemd was `running`; graphical and multi-user targets, GDM, Docker, and NFS were active; RDMA port 20049 was ready; and no units failed. Full verification passed routes, exact mounts, integrity, and client-replica absence on all three ranks. The raw collector captured 392,000 bytes and all hashes verify. A separate inventory probe falsely reported the owner alias unreachable even though the confirmed control endpoint and full fabric check passed; inventory bypasses the shared confirmed-endpoint resolver and needs a regression fix. Repeat one clean connected-display boot, then rerun the physical owner-reboot storage and serving gate. |
| 2026-08-08T23:28Z | Inventory confirmed-endpoint regression fix | Routed inventory's injectable remote probe through the shared confirmed-endpoint resolver while retaining the saved alias as the SSH and host-key identity. Added a deterministic three-node test whose SSH shim rejects each remote alias unless the expected manifest `HostName` and `HostKeyAlias` options are present. | **PASS — FIXED IN TESTS AND ON HARDWARE.** The topology regression, 77-case inventory classifier, fail-closed probe suite, and complete `scripts/selftest.sh` pass. During the live check, owner alias resolution still failed, but patched inventory reached all three ranks through their recorded control addresses and reported every probe healthy, with no managed services or unmanaged GPU processes. This closes the false-unreachable diagnostic defect without weakening fail-closed behavior. |
| 2026-08-08T23:56Z | Repeat controlled owner boot with a real display | Kept the same real display powered and connected, rebooted only owner rank 1 again, and observed a changed boot identity. The original 15-minute watcher expired before the attended command, so this run intentionally claims no observed offline interval. Captured exact unit timing, a new three-rank private forensic set with verified hashes, full fabric integrity, and patched final inventory. | **PASS — CONNECTED-DISPLAY BOOT POLICY REPEATED.** One DRM output was connected, GDM registered its greeter on `seat0`, and Plymouth wait completed automatically with `Result=success` in 3.252 seconds. Systemd reached `running`; graphical and multi-user targets, GDM, Docker, and NFS were active; RDMA port 20049 was ready; and no units failed. Full verification passed routes, exact mounts, integrity, and replica absence on all three ranks. Final inventory found all three probes healthy with no managed or unmanaged GPU work. Together with the first 3.218-second run, this establishes repeatability for the connected-display policy. The headless issue remains open, and the physical owner-reboot storage and serving gate still must be rerun. |

## Promotion checklist

- [x] Replicated-local cold I/O baseline
- [x] Two-node cold fabric I/O with HCA-counter traffic proof
- [x] Cold startup timing
- [x] Repeated-start determinism and correctness comparison
- [x] Appropriate long-context gate
- [x] Interrupted load and recovery
- [x] RoCE link-loss recovery
- [x] NFS service restart recovery
- [ ] Owner reboot recovery
- [ ] Restart loop and sustained soak
- [x] Client durable-replica absence proof
- [ ] Final self-test, inventory, lifecycle, and privacy audit

## Findings and operator learnings

1. Existing cache placement can make owner selection objective: choose the
   confirmed serving rank that already owns the complete sealed snapshot. This
   avoids both a redundant download and an arbitrary hostname-based choice.
2. A storage configuration may be valid while deliberately unapplied. Route,
   replica-absence, mount, integrity, and owner-service checks remain distinct,
   which made the current `owner-unready` diagnosis specific and actionable.
3. The documented idle-cluster requirement is operationally significant. An
   unrelated managed service can leave enough memory for health checks while
   still making global page-cache eviction and cold-start evidence invalid and
   disruptive.
4. Package readiness and privilege readiness are separate. All required
   software is installed, but attended sudo is still required for exports,
   mounts, cache eviction, and teardown. The fail-closed default preserves the
   site's privilege policy.
5. Exact-profile teardown provided a useful safety boundary for maintenance:
   it removed the two expected rank containers, verified their absence, and
   left no unmanaged GPU workload. Memory recovery across every rank supplied
   an independent confirmation that the serving allocation was released.
6. Worker checkouts are unnecessary for this workflow. The controller streams
   Python and privileged shell payloads over SSH and serializes remote Docker
   commands. Remote requirements are runtime capabilities, the selected image,
   and the configured paths—not synchronized Git working trees.
7. A successful full checksum immediately after setup proves content identity,
   mount correctness, and readable transport state. It does not establish cold
   throughput because page-cache state was not controlled for that check.
8. Attended authentication is intentionally not an automation credential. A
   successful interactive operation does not make later tool sessions
   passwordless, so runbooks should group privileged work deliberately while
   preserving a human at each sudo boundary.
9. A successful read plus negligible control-LAN traffic is not sufficient
   transport proof by itself. The preserved failure was correct to block
   promotion because its configured positive counter did not observe the
   payload, even though subsequent diagnostics identified the counter—not the
   route—as the defect.
10. Linux Ethernet netdevice statistics are the wrong positive byte source for
    this RPC/RDMA path. ConnectX HCA `port_*_data` counters observed the transfer
    symmetrically at the client and owner; their values must be converted from
    four-octet units before threshold comparison. Control-LAN bounds can remain
    netdevice-based.
11. Traffic evidence needs provenance at the counter level. Recording both the
    configured netdevice and HCA, plus whether a value came from netdevice bytes
    or converted HCA port-data units, makes the proof reviewable and prevents a
    plausible-looking counter from an adjacent rail from being accepted.
12. The passing physical result shows expected protocol overhead rather than an
    exact logical-byte match: HCA traffic was about 5.7% above the checkpoint
    payload and was symmetric between client RX and owner TX. Thresholds should
    continue to allow protocol overhead while requiring model-sized transfer.
13. Replicated and single-copy readiness are intentionally mutually exclusive
    on a client. A complete temporary baseline cache makes replicated checks
    pass but causes fabric readiness to fail with `replica-present`; this
    prevents a benchmark convenience copy from silently invalidating the
    single-copy claim.
14. Similar JSON field names do not guarantee identical types across commands.
    Fabric readiness returns a `nodes` record list, while replicated
    `check-weights` returns an integer `nodes` count plus a `ranks` list. The
    regression suite must exercise the complete shell workflow and real command
    schema, not only the downstream Python report builder.
15. An independent Grok 4.5 high review agreed that HCA port-data counters are
    the correct positive signal on this ConnectX RPC/RDMA path and that the
    combined evidence is credible for a two-node storage canary. It also
    emphasized that HCA bytes prove traffic on the configured port during the
    read window, not exclusive attribution to NFS/RDMA without the accompanying
    idle-cluster, RDMA-mount, symmetry, and cache-growth evidence.
16. Positive traffic gates should constrain implausibly large observations as
    well as require a minimum. The current fabric check accepts model-sized
    lower bounds but does not yet enforce client-RX/owner-TX symmetry or cap
    unrelated HCA traffic; these are hardening requirements before promotion.
17. Configuration identity and live device binding are different guarantees.
    Report generation rejects an unexpected configured HCA name, but sampling
    should also prove that the configured netdevice is adjacent to that HCA and
    should resolve the usable HCA port rather than assuming `ports/1`.
18. Counter reset or wrap currently fails closed because a negative delta is
    rejected. Persisting the raw four-octet value and scale, or documenting the
    conversion and reset policy explicitly, would make future counter evidence
    easier to audit.
19. A low-level launcher test does not prove its public wrapper exposes the
    same options. `cluster/start-cluster.sh --skip-warmup` was tested while
    `scripts/up.sh` rejected the documented option. Wrapper-boundary argument
    acceptance and forwarding now have their own regression.
20. Cold-start preflight must include exact image presence on every serving
    rank before page-cache eviction. Repairing a missing image after eviction
    wastes the cold window and can warm filesystem state, so image readiness is
    now treated as a prerequisite to the final privileged cache drop.
21. Host root and container root do not bypass an NFS server's `root_squash`
    policy. Exporting a private Hugging Face home with its normal
    `0700`/`0600` modes can make the snapshot readable to the cache owner
    and ordinary validation process while remaining invisible to rootful vLLM
    on a client.
22. The narrowest safe compatibility fix is an identity mapping on the exact
    model repository export—not `no_root_squash` and not a mapping on the
    whole Hugging Face home. Mapping the anonymous request to the repository's
    verified non-root owner preserves the root-squash boundary while excluding
    tokens and unrelated models from both NFS and the container.
23. Replacing a storage configuration is also a live-kernel-state migration.
    Absence of the generated export file is insufficient because an export can
    remain active until `exportfs` refreshes it. Replacement now requires the
    old export file, active export-table entry, and every configured client
    mount to be absent after teardown.
24. Validating only the new export does not prove the broader cache stopped
    being exported. A safe apply must reject stale Pulsar export files and any
    active export whose path is the Hugging Face home or another parent of the
    selected repository.
25. The repository directory owner is not sufficient access evidence. The
    anonymous NFS identity must be able to traverse every nested directory,
    read every regular file and resolved link target, and reject any symbolic
    link that escapes the exported repository before NFS state changes.
26. Apply is a transaction across one owner and multiple clients even though
    the operating system offers no distributed transaction. Once the first
    owner file is written, failures must trigger best-effort removal of this
    config's exact mounts and export; incomplete cleanup must remain a visible
    blocker requiring teardown.
27. Least-privilege tests need decoy content. A sibling model repository and
    token file in the fixture make it explicit that neither the generated
    export nor the Docker volume arguments expose content outside the selected
    model subtree.
28. A client-side NFS probe and an owner-local bind probe do not have identical
    Unix identity behavior. With all capabilities removed, client container
    root still reads through the configured root-squash owner mapping, while an
    owner container needs the launcher's normal root DAC override to traverse
    the cache owner's private directory ancestry. Probes must match launcher
    capabilities before treating that difference as a storage failure; the
    exact bind remains read-only in both cases.
29. Scope claims need evidence at every boundary. Configuration validation,
    the generated export file, the active kernel export table, client mount
    targets, and the container namespace now independently agree on the same
    selected repository subtree. A passing check at only one of those layers
    would not prove that parent-cache or credential exposure was eliminated.
30. Cold storage throughput is not a proxy for time to first health. The model
    snapshot can be read in seconds while container startup, distributed
    initialization, model construction, and first-health readiness together
    took 126.462 seconds. Both measurements are useful, but they answer
    different operational questions and must remain separate artifacts.
31. A post-health smoke completion proves that the API and distributed
    execution path answer after startup; it does not establish deterministic
    output or model correctness. Those claims require the repository's
    repeatable capture and comparison gates while this exact service remains
    running.
32. Container liveness is not engine health. Both rank containers remained
    `Up` after the engine core died, while the health endpoint was unavailable.
    Lifecycle and soak checks must use API health in addition to Docker state.
33. Validation failures are evidence only when diagnostics survive. Redirecting
    greedy-capture stderr to `/dev/null` combined with `set -e` erased the
    failing request and bypassed the runner's summary. Per-capture stderr is now
    a named artifact, and a capture failure terminates with an explicit verdict.
34. Successful cold loading does not validate the CUDA-graph execution path.
    The model loaded from the exact fabric subtree, reached health, and answered
    smoke requests before the same stock-v0.26.0 cross-node sampling timeout
    already documented elsewhere in this repository appeared under sequential
    capture. A controlled `--enforce-eager` retry separates runtime stability
    from weight-storage behavior before any permanent profile change.
35. A workaround should become a profile default only after the override is
    proven on physical hardware. The eager override passed strict captures,
    concurrency, long context, and post-gate health first; a second boot then
    proved that the profile itself supplied the same behavior without an
    environment override.
36. Warm eager startup at 94.566/94.657 seconds and cold graph-mode startup at
    126.462 seconds are different experiments. Cache state and execution mode
    both changed, so the numbers document repeatability and operational timing
    but do not establish an eager-versus-graph startup speedup.
37. Console-only success is not durable evidence. The context runner now writes
    a compact JSON artifact containing requested context, measured prompt
    tokens, depths, expected values, responses, and verdicts while deliberately
    omitting the large generated haystack.
38. Same-boot and cross-boot bit identity answer different questions. Two
    captures were identical within each eager boot, and the second boot was
    identical to the first boot's baseline. That establishes restart
    determinism for this image/configuration. That result alone did not prove
    storage-source parity; the later replicated-serving control tests it as a
    separate claim.
39. A cold timing belongs to the exact final execution configuration. The
    graph-mode launch proved cold model-subtree loading but later failed under
    inference; after eager became the profile default, a second cache-controlled
    launch was required. Its 105.731-second result is the valid cold timing for
    the stable canary, while the graph-mode number remains failure-path history.
40. Weight placement affects loading, not resident model semantics. With image,
    profile, ranks, and eager execution held constant, replicated and fabric
    serving produced the same 30 greedy outputs with zero logprob delta and
    effectively identical c=8 throughput. Cold I/O and first-health metrics
    remain separate experiments and must not be inferred from resident decode.
41. The single-copy invariant must stay mutually exclusive with benchmark
    convenience state. Staging the temporary serving replica deliberately made
    fabric readiness fail closed with `replica-present`; replicated launch was
    allowed only through its explicitly selected source.
42. An A/B is incomplete until its temporary state is reversed and independently
    checked. The replicated service was stopped, only client-role copies were
    purged, the authoritative owner copy was preserved, full fabric checksum and
    route verification passed again, and idle inventory found no residual work.
43. A cleanup function does not protect operator interruption unless signals are
    wired to it. Explicit startup failures already removed immutable IDs, but a
    terminal interrupt bypassed that path until `INT`, `TERM`, and `HUP` gained
    the same launch-scoped handler.
44. Interrupted-start evidence needs a negative proof as well as a successful
    relaunch. Exit 130, absence of a startup metric or health marker, immediate
    idle inventory, and removal of exactly the recorded IDs establish that no
    partial service was promoted; checksum verification and strict post-recovery
    captures establish that cleanup did not damage storage or model behavior.
45. A stable SSH alias is an identity, not proof of the network path used to
    reach it. Name resolution moved the alias onto a RoCE address even though
    the confirmed manifest recorded an independent control address. Confirmed
    weight-fabric operations must use the alias for SSH user/config and host-key
    continuity while forcing the connection endpoint to the recorded control
    address.
46. Fault evidence must survive the failure being injected. Rank stdout/stderr
    can contain site-private values, so publishing raw in-flight files is
    unsafe; deleting the whole stage on failure is equally unacceptable.
    Private staging followed by exact-value redaction, a structured failure
    record, and the normal privacy audit preserves reviewable evidence without
    publishing hostnames, addresses, or paths.
47. A paced fault run is an availability experiment, not a performance or
    recovery-latency benchmark. This outage overlapped the rate limiter's
    deliberate idle budget, so total duration stayed at the configured cap.
    The valid proof is the timestamped outage inside the client read window,
    completion of that exact read after restoration, model-sized symmetric HCA
    traffic, post-fault checksums, and unchanged serving outputs.
48. Runtime staging paths can be deliberately non-executable. This owner's
    `/run` is mounted `noexec`, so a transient unit cannot use a staged script
    there as its direct `ExecStart` even when the file mode is executable.
    Launching the bounded payload as `/bin/bash /run/<script>` preserves the
    noexec policy and the self-restoring trap. A rejection before the trigger,
    cache drop, or client read is an arming failure—not NFS recovery evidence.
49. Storage recovery and serving recovery are separate gates. Completion of the
    exact client read after NFS/RDMA restoration proves that the hard mount
    resumed without a replica or control-LAN fallback; full verification alone
    does not prove that vLLM can load and serve afterward. The separate relaunch,
    smoke, strict baseline comparison, concurrency sweep, fatal-log scan, exact
    teardown, and final idle check close that end-to-end claim.
50. Storage-service readiness and full operating-system boot completion are also
    separate predicates. Docker, NFS/RDMA, the exact export, and a hard-mounted
    client read can recover while `systemctl is-system-running` remains
    `starting` because of an unrelated boot target. Conjoining those predicates
    hid the useful storage-recovery timestamp and made the first monitor's error
    less specific; future harnesses record and report them independently.
51. Reboot evidence cannot reuse same-boot HCA delta assumptions because the
    device counters reset with the owner. The valid cross-boot proof is a changed
    boot identity, an observed offline interval, completion of the exact client
    snapshot that began before reboot, restored NFSv4.2/RDMA and export state,
    full manifest integrity, and continued client-replica absence.
52. Recovery timing must reflect the granularity actually captured. The client
    completion proves that NFS/RDMA recovered no later than 62.974 seconds after
    the reboot command, but the conjoined first monitor did not preserve the
    exact service-ready instant. The public result therefore records an upper
    bound rather than fabricating a point estimate.
53. The incomplete boot was not an NFS failure or a unique noisy run. Journal
    history shows `plymouth-quit-wait.service` remaining activating long after
    the display manager started on the owner's current and previous boots and
    on another serving rank, while the third storage rank completed it normally.
    An attended start of the standard Plymouth quit service completed the boot,
    but that intervention makes this attempt a failed automatic-recovery gate.
    A boot-mode remediation needs explicit operator approval before a clean
    retry; repeatedly rebooting the unchanged configuration is not promotion
    evidence.
54. The observed handoff mechanism and its root trigger are different claims.
    Side-by-side forensics rule out package versions, semantic kernel options,
    GRUB/GDM configuration, on-disk unit fragments, and relevant drop-ins as the
    rank split. Both affected ranks lack a boot framebuffer/connected output;
    the unaffected rank has both. This makes display/DRM presence the strongest
    trigger hypothesis, but only a controlled reboot can establish causality.
55. Masking a wait unit can make systemd look complete without completing the
    resource handoff it was intended to observe. `plymouth-quit-wait.service`
    therefore must not be masked as a production fix. Likewise, adding Wants or
    ordering around GDM's deliberate `Conflicts=plymouth-quit.service` can create
    a self-stopping or circular transaction. A helper is viable only if it calls
    the Plymouth quit operation without conflicting with GDM and passes isolated
    boot/greeter validation first.
56. Cross-node diagnostic evidence must be publishable without disclosing the
    site. The raw 388,912-byte capture remains in the ignored private tree with
    per-rank hashes. Its public JSON records ranks, durations, booleans, shared
    version/configuration hashes, interpretation, and uncertainty; the standard
    artifact audit rejects private identities, paths, and fields.
57. GDM's shipped unit explicitly replaces `plymouth-quit.service` and promises
    to stop Plymouth itself. On the unaffected rank, GNOME Shell logs session
    registration 27 milliseconds before Plymouth wait finishes. The affected
    ranks start GDM/Xorg in empty `NULL` display mode but never register that
    session, so the promised quit never occurs. This sequence is stronger than
    merely observing both services active.
58. An active `graphical.target` does not prove graphical recovery. Starting the
    standard Plymouth quit unit honors its declared conflict by stopping GDM;
    systemd can then report `running` with zero failed units while the display
    manager is inactive and no graphical session exists. The prior recovery
    artifact's `full_boot_after_intervention=pass` is therefore limited to
    systemd target completion, not GUI health.
59. A connected display at boot is now the most evidence-aligned GUI-preserving
    controlled test. A real display should be tried before an emulator, and one
    successful boot is not promotion evidence. Removing `splash` can prevent
    Plymouth from starting and avoid the wait, but it does not by itself prove
    that the headless GDM greeter registers or that later display attachment is
    usable. A direct-quit helper has the same limitation.
60. The package update/load warning is not causal. The affected local rank and
    unaffected storage rank both have older unit fragments loaded, while the
    freshly rebooted affected owner loaded the current fragments and still
    hung. On-disk fragments and installed versions match across all three ranks.
61. The controlled display intervention changed the outcome on the same owner
    without changing packages, kernel options, targets, or service policy. With
    a real display present before reboot, the owner exposed one connected DRM
    output, registered the GDM greeter on `seat0`, and completed the
    GDM-to-Plymouth handoff automatically. This raises the display-trigger
    hypothesis from cross-rank correlation to strong single-run causal evidence,
    but repetition is still required.
62. A completed oneshot is not necessarily `inactive`.
    `plymouth-quit-wait.service` finished successfully after 3.218 seconds and
    remained `active (exited)`. Recovery monitors must evaluate `Result`,
    `SubState`, and exit status rather than treating `systemctl is-active`
    alone as proof that the wait is still executing.
63. Inventory does not yet honor the confirmed control endpoint everywhere.
    Its private remote helper invokes the saved SSH alias directly, while the
    shared helper pins transport to the manifest control address. When the alias
    stopped resolving after this reboot, two inventories reported a false
    unreachable state even though direct confirmed-path SSH and full fabric
    verification passed. Route inventory through the shared resolver and cover
    this with a regression test before relying on inventory during rail faults.
64. A connected display is an operational mitigation, not a headless solution.
    The controlled boot must pass once more and the physical owner-reboot fault
    must be repeated with the display attached before the owner-reboot promotion
    box can be checked. Nodes expected to remain truly headless still need a
    separately validated policy.
65. Diagnostic control traffic needs the same endpoint guarantees as fault
    traffic. Inventory retained an injectable SSH binary but now delegates
    endpoint selection to the shared resolver. A deterministic shim rejects
    unpinned aliases, and the live test passed while the saved owner alias was
    genuinely unresolved. This proves the fix rather than merely exercising a
    normally functioning resolver.
66. The connected-display result is repeatable on the same owner. Two clean
    boots completed the GDM-to-Plymouth handoff in 3.218 seconds and 3.252
    seconds with the same boot policy, active GDM greeter, zero failed units,
    and fully verified NFS/RDMA storage. This is sufficient to proceed to the
    physical owner-reboot fault with the display attached, but it does not make
    headless graphical boot reliable.
67. An attended reboot watcher must be armed close to the password-gated
    command. The second watcher's 15-minute bound expired before the operator
    issued the reboot. A changed boot identity still proves a new boot, but no
    offline interval is inferred or published for that run.

## Final disposition

Pending. Do not promote `--weight-source fabric` based on this file alone.
