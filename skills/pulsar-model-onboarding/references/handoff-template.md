# Onboarding handoff

Fill every line. Write "none" rather than leaving a field empty. This
handoff is not a review decision and assigns no status.

## Identity

- Model: `<org/name>`
- Exact revision: `<commit>`
- Receipt id: `<digest>`
- Spec id: `<64-hex>` (measured spec at `<repository-relative path>`)
- Image digest: `<sha256>`
- Geometry: `<nodes> node(s)`; platform `dgx-spark-gb10`

## Evidence

- Run directory: `results/baseline-v1/<spec_id>/`
- Gates passed, in policy order: `<list>`
- Gate that stopped the run, if any: `<gate>` with its closed measurement
- Proposed review status (evaluator): `stable` or `failed`
- Interrupted measurements (kept as they were written; nothing invented):
  `<list or none>`

## Promotion

- Promotion pull request: `<number>` or "not opened"
- `docs/MODELS.md` block regenerated: yes/no
- Privacy scan and full selftest on the head: yes/no

## Boundaries

No review status was assigned by this skill. Nothing was written under
`releases/` except through the promotion command inside the pull request.
No physical behavior is claimed beyond what the run directory records.

## Cleanup

- Services stopped through the normal stop path: `<spec_id>` or none
- Library resources removed (only those this workflow created): `<list>`
- Resources left in place and why: `<list>`
