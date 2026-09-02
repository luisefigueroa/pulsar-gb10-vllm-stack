# Baseline-v1 policy

This directory holds the lab-wide baseline-v1 policy. `baseline-v1.json` is
the closed document the evaluator hashes and copies into a measured spec's
`measurements[]`.

The committed GSM8K dataset revision and file digest are obviously fake
placeholders. Freeze a real dataset id, revision, and file SHA-256 in this
policy before the first physical baseline job. Changing those pins changes
the policy digest.

`accuracy_floor_overrides` is keyed by exact Hugging Face model id. An empty
object means every spec uses the default accuracy floor in the gsm8k-subset
thresholds. Overrides are part of the hashed policy.

Do not pretty-print this file by hand. The loader fails if the on-disk bytes
differ from `release_spec.pretty_json_bytes` of the verified object.
