#!/usr/bin/env python3
"""Promote a measured ADR 0017 spec into a released document.

  python3 scripts/release_spec_promote.py MEASURED --reviewer NAME --out FILE
      [--reviewed-at ISO-8601-UTC-Z] [--status stable|failed|withdrawn|validated]

The output is the measured document with ``state`` set to ``released`` and a
``review`` block; nothing else changes. Without ``--status`` the status is
``stable`` when every baseline-v1 outcome is ``pass`` and ``failed``
otherwise. ``verify_spec`` refuses ``stable`` or ``validated`` unless the
recorded outcomes support it. The file is written only if ``--out`` does not
exist; committing it under ``releases/`` is the reviewed promotion PR.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from release_spec import ReleaseSpecError, load_spec, pretty_json_bytes, verify_spec  # noqa: E402
from release_spec.schema import REVIEW_STATUSES  # noqa: E402

_VALIDATE_DIR = _REPO_ROOT / "validate"
if str(_VALIDATE_DIR) not in sys.path:
    sys.path.insert(0, str(_VALIDATE_DIR))
from baseline_v1_policy import EXPECTED_GATES  # noqa: E402

BASELINE_CRITERIA = frozenset(criterion for criterion, _operation in EXPECTED_GATES)


class PromoteError(ValueError):
    """The measured spec cannot be promoted as requested."""


def fail(message: str) -> None:
    raise PromoteError(message)


def baseline_suite(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the complete baseline-v1 suite or fail when it is partial.

    ``stable`` means every baseline-v1 criterion the policy names passed under
    one policy digest. A document carrying fewer criteria is not a judged run.
    """
    baseline = [m for m in spec["measurements"] if m["suite"] == "baseline-v1"]
    if not baseline:
        fail("measured spec has no baseline-v1 measurements; run the evaluator first")
    criteria = {m["criterion_id"] for m in baseline}
    if criteria != BASELINE_CRITERIA or len(criteria) != len(baseline):
        missing = sorted(BASELINE_CRITERIA - criteria)
        extra = sorted(criteria - BASELINE_CRITERIA)
        fail(
            "measured spec does not carry the exact baseline-v1 suite "
            f"(missing={missing}, extra={extra}); a partial run is not promotable"
        )
    if len({m["policy_digest"] for m in baseline}) != 1:
        fail("baseline-v1 measurements were judged under more than one policy digest")
    return baseline


def default_status(spec: dict[str, Any]) -> str:
    baseline = baseline_suite(spec)
    return "stable" if all(m["outcome"] == "pass" for m in baseline) else "failed"


def promote(
    spec: dict[str, Any],
    *,
    reviewer: str,
    reviewed_at: str,
    status: str | None,
) -> dict[str, Any]:
    if spec["state"] != "measured":
        fail("only a measured spec can be promoted")
    if status is None:
        status = default_status(spec)
    if status not in REVIEW_STATUSES:
        fail(f"unsupported review status {status!r}")
    if status in {"stable", "validated"}:
        baseline_suite(spec)
    document = dict(spec)
    document["state"] = "released"
    document["review"] = {
        "status": status,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
    }
    try:
        return verify_spec(document)
    except ReleaseSpecError as exc:
        fail(str(exc))
    return document


def utc_now_z() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("measured", help="measured spec written by the evaluator")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--reviewed-at", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = pathlib.Path(args.out)
    try:
        if out.exists() or out.is_symlink():
            fail(f"refusing to overwrite {out}")
        spec = load_spec(args.measured)
        released = promote(
            spec,
            reviewer=args.reviewer,
            reviewed_at=args.reviewed_at or utc_now_z(),
            status=args.status,
        )
        expected_name = f"{released['spec_id']}.json"
        if out.name != expected_name:
            fail(f"--out must be named {expected_name}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(pretty_json_bytes(released))
    except (PromoteError, ReleaseSpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"promoted {released['spec_id']} state=released "
        f"review.status={released['review']['status']} -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
