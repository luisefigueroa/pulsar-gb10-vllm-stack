# Release specs

This directory holds **released** ADR 0017 specs. One file per identity,
named `<spec_id>.json`. A spec is promoted only by a reviewed pull request
that copies a lab `measured` document here with `state=released`.

Git history of each file is identity lineage. Lifecycle is `review.status`.
Do not put site values (hostnames, node ids, paths, credentials) in these
files. The stack verifies every file on read; a bad file fails without
fallback.

The directory may be empty. `models/*.conf` remains the operator start path
through Stage 3. `scripts/release.sh` (`./pulsar release verify|show|list`)
reads this directory. It does not start a server or grant serving permission.
`PULSAR_RELEASES_ROOT` is a test override naming this directory.

A released spec id (the 64-hex file name) is accepted wherever a profile
name is: `./pulsar start|status|stop <spec_id>` and the model-library
lifecycle commands `prepare`, `pin`, `unpin`, and `purge-hot <spec_id>`.
The spec's sealed snapshot manifest is the reviewed file list those commands
verify against. A spec reuses a prepared view of the same model, commit, and
manifest under any name; only a new view is named by spec id. Prepare, pin,
and launch take the first matching view that verifies on every serving rank
and is bound to the current durable home; `purge-hot <spec_id>` removes every
matching view on the target ranks, incomplete ones included, honors pins
first, and may name a previous placement with `--node`. Spec presence is
never occupancy, and `review.status` is display only.
