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
A spec's prepared view is keyed by the spec id exactly as a conf's view is
keyed by its name: one name, one directory, and no sharing between names.
`prepare <spec_id>` resolves the catalog entry for the spec's model and
commit, requires the spec's sealed snapshot manifest to equal the durable
home's download receipt, and materializes the view under the spec id; a
view prepared under a conf name is not reused, so a conf and its spec on
the same non-home rank hold two working copies while both exist. Prepare,
pin, and launch verify a spec view against the sealed manifest id, not a
conf file; `purge-hot <spec_id>` removes the spec's own view on the target
ranks and may name a previous placement with `--node`. Spec presence is
never occupancy, and `review.status` is display only.
