# Candidate Exchange Schema

Workers should return JSON with this conceptual shape. Exact serialization may vary, but no field required for validation should be omitted.

```json
{
  "snapshot": {
    "repository_root": "string",
    "commit": "string",
    "branch_or_detached": "string",
    "dirty": true
  },
  "workflow_status": [
    {
      "workflow": "string",
      "status": "solid | candidates | not_applicable | not_statically_verifiable",
      "summary": "string",
      "evidence": [
        {"path": "string", "line_start": 1, "line_end": 1}
      ]
    }
  ],
  "candidates": [
    {
      "candidate_id": "stable-local-id",
      "workflow": "string",
      "gap_type": "missing | unreachable | undocumented | hidden-prerequisite | contract-mismatch | incomplete-recovery | non-reproducible-validation | minor-drift",
      "proposed_priority": "P1 | P2 | P3",
      "confidence": "high | medium | low",
      "title": "user-visible consequence",
      "claim_authority": "supported/current | experimental | proposed/roadmap | historical/superseded | ambiguous",
      "doc_claims": [
        {
          "path": "string",
          "line_start": 1,
          "line_end": 1,
          "summary": "paraphrased claim"
        }
      ],
      "implementation_evidence": [
        {
          "path": "string",
          "line_start": 1,
          "line_end": 1,
          "role": "entry point | caller | callee | state producer | state consumer | config | recovery | counterevidence"
        }
      ],
      "trace": [
        "operator begins with ...",
        "documentation directs ...",
        "entry point calls ...",
        "required state is absent or mismatched ...",
        "operator consequence ..."
      ],
      "negative_evidence": {
        "required_for": "missing or unreachable only",
        "expected_capability": "string",
        "search_terms": ["string"],
        "locations_inspected": ["string"],
        "closest_implementation": "string",
        "why_insufficient": "string"
      },
      "counterevidence_checked": ["string"],
      "operator_consequence": "string",
      "suggested_fix": "one or two sentences",
      "unresolved_runtime_dependency": "string or null"
    }
  ],
  "files_fully_read": ["repository-relative/path"],
  "open_questions": [
    {
      "question": "string",
      "why_material": "string",
      "evidence_needed": "string"
    }
  ]
}
```

Low-confidence candidates do not become final findings. Preserve them only as material open questions.
