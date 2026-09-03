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
