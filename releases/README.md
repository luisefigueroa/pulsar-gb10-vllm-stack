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

Serving a spec needs the gitignored deployment overlay
`.pulsar-overlay.json` at the repository root. It holds only deployment
values, never recipe fields. The smallest valid overlay serves every spec on
port 8000 under its Hugging Face model id on this node:

```json
{
  "schema_version": 1,
  "kind": "pulsar-deployment-overlay",
  "defaults": {
    "port": 8000,
    "served_name": null,
    "cache_root": null,
    "placement": null
  },
  "specs": {}
}
```

Every key is required; `null` keeps the default (`served_name` null means
the model id). A per-spec entry under `specs` keyed by spec id overrides the
defaults, and `placement` is `{"node_id": "<topology node id>"}` when the
spec must run on one confirmed node. `scripts/release_consumer.py` owns the
schema and fails without fallback on an unknown key.

A released spec id (the 64-hex file name) is accepted wherever a profile
name is: `./pulsar start|status|stop <spec_id>` and the model-library
lifecycle commands `prepare`, `pin`, `unpin`, and `purge-hot <spec_id>`.
A spec's prepared view is keyed by the spec id exactly as a conf's view is
keyed by its name: one name, one directory, and no sharing between names.
`prepare <spec_id>` resolves the catalog entry for the spec's model and
commit, requires the spec's reviewed snapshot manifest to equal the durable
home's download receipt, and materializes the view under the spec id; a
view prepared under a conf name is not reused, so a conf and its spec on
the same non-home rank hold two working copies while both exist. Prepare,
pin, and launch verify a spec view against the spec snapshot manifest id, not a
conf file; `purge-hot <spec_id>` removes the spec's own view on the target
ranks, including one an interrupted preparation left incomplete, and may
name a previous placement with `--node`. Preparation is all-or-nothing: a
multi-rank view lost on one rank is re-materialized on every rank by
`purge-hot <spec_id> --yes --force-unpin` followed by `prepare <spec_id>
--yes`. Spec presence is never occupancy, and `review.status` is display
only.
