"""ADR 0017 release spec schema, spec_id, and verifier.

This package is standard-library-only and importable from the repository
root. It imports nothing from ``scripts/``. ``spec_id`` hashes the identity
block with ``json.dumps(..., sort_keys=True, separators=(",", ":"),
ensure_ascii=False)``. Nested snapshot ``manifest_id`` copies the model-library
algorithm and omits ``ensure_ascii=False``. ASCII snapshot paths make the
two encodings agree.
"""

from .identity import identity_block, snapshot_file_lists_equal, spec_id_for
from .normalize import (
    build_snapshot_manifest,
    canonical_json_digest,
    normalize_container_env,
    normalize_engine_args,
    normalize_snapshot_files,
    pretty_json_bytes,
    snapshot_manifest_id,
)
from .schema import (
    KIND,
    REVIEW_STATUSES,
    SCHEMA_VERSION,
    SNAPSHOT_MANIFEST_KIND,
    STATES,
    ReleaseSpecError,
)
from .verify import load_spec, verify_spec

__all__ = [
    "KIND",
    "REVIEW_STATUSES",
    "SCHEMA_VERSION",
    "SNAPSHOT_MANIFEST_KIND",
    "STATES",
    "ReleaseSpecError",
    "build_snapshot_manifest",
    "canonical_json_digest",
    "identity_block",
    "load_spec",
    "normalize_container_env",
    "normalize_engine_args",
    "normalize_snapshot_files",
    "pretty_json_bytes",
    "snapshot_file_lists_equal",
    "snapshot_manifest_id",
    "spec_id_for",
    "verify_spec",
]
