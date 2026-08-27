# Commit safety and publishable privacy

Pulsar uses one privacy scanner at three layers:

1. `scripts/check_publishable_privacy.py` scans the working tree during
   selftest and publication review.
2. `.githooks/pre-commit` scans the exact staged blobs before a local commit.
3. `skills/pulsar-safe-commit/` requires the same staged scan when Codex
   creates a commit, even if the hook is not installed.

The full `scripts/selftest.sh` entrypoint calls the scanner, so CI or another
publication system that runs the full suite enforces the same policy without
depending on a developer's local Git configuration.

## Protected information

High-confidence credential and SSH key material is rejected in all staged
source files except dedicated test fixtures. Publishable Markdown, `results/`,
`bench/results/`, and the tracked Model Serving Release registry additionally
must not contain:

- stable or site-local hostnames;
- non-documentation IP addresses in network context;
- SSH aliases, host keys, public/private keys, known-host entries, or
  fingerprints;
- durable node, machine, host, or topology identifiers;
- site interface and filesystem identity;
- user-specific home/workspace paths; or
- credential-shaped Hugging Face, GitHub, OpenAI, AWS, or Google tokens.

Use `Node A`, `Node B`, and runtime roles such as `rank 0`. Documentation
may use loopback, wildcard, and RFC documentation addresses. Structured fields
that a schema cannot omit must use an explicit redaction marker such as
`"<redacted>"`; do not substitute a realistic-looking fake site identifier.

## Install the local hook

The hook is tracked but Git does not enable repository hooks automatically.
Install it for this checkout with:

```bash
git config --local core.hooksPath .githooks
git config --local --get core.hooksPath
```

The expected value is `.githooks`. This is defense in depth: local hooks can
be absent or bypassed, so required CI should run `scripts/selftest.sh`.

## Run the gates directly

```bash
# Tracked and nonignored untracked publishable files
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check_publishable_privacy.py

# Exact staged blobs, including partially staged files
PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/check_publishable_privacy.py --staged
```

A failure is not an allowlist request. Remove or redact the value, or keep the
artifact in protected/ignored storage. Add a new allowance only for a
documented public representation, with adversarial tests proving the sensitive
form still fails.

Deleting a file from the current tree does not erase it from Git history. Treat
history rewriting as a separate, explicit incident-response decision.
