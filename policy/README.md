# Baseline-v1 policy

This directory holds the lab-wide baseline-v1 policy. `baseline-v1.json` is
the closed document the evaluator hashes and copies into a measured spec's
`measurements[]`.

The GSM8K pins are frozen to the public `openai/gsm8k` dataset at commit
`740312add88f781978c0658806c59bc2815b9866`, file
`main/test-00000-of-00001.parquet`, whose SHA-256 is
`ee7b8da9e381df27b9e3f7758a159ab2bdaa4dbaa910546cbbc47e0cb44e4f59` (the
digest Hugging Face publishes for that file at that commit). Fetch that
exact file yourself, confirm the digest, and pass it to
`validate/gsm8k_eval.py --dataset`; the file is not committed. Changing any
pin changes the policy digest, so every spec measured afterwards records a
different `policy_digest`.

`accuracy_floor_overrides` is keyed by exact Hugging Face model id. An empty
object means every spec uses the default accuracy floor in the gsm8k-subset
thresholds. Overrides are part of the hashed policy.

Do not pretty-print this file by hand. The loader fails if the on-disk bytes
differ from `release_spec.pretty_json_bytes` of the verified object.
