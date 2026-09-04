# Baseline-v1 evidence

One directory per measured ADR 0017 spec, named by `spec_id`. Each holds
the six closed validator measurements the evaluator reads
(`verify-snapshot-manifest.json`, `serve-smoke.json`,
`compare-captures.json`, `evaluate-gsm8k.json`, `validate-soak.json`,
`benchmark-serving.json`) and `spec.json`, the measured spec after
`validate/baseline_v1.py` filled `measurements[]` and `evidence[]`.
`evidence[].sha256` is the digest of each measurement file and
`evidence[].lab_commit` names the checkout that produced it.

Raw captures and bench output from `validate/run-gates.sh` and the soak
summary stay flat under `results/` by served name and tag, as before. The
measurement documents are status-neutral; the released copy of a spec lives
under `releases/` only after a reviewed promotion PR.
