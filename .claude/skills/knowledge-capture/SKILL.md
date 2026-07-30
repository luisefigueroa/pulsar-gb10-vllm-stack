---
name: knowledge-capture
description: Capture a finding into the shared OKF knowledge base. Use IMMEDIATELY after any of these: running a benchmark or hardware measurement; hitting an error with distinctive text; trying something that did NOT work; eliminating a hypothesis; discovering version-specific behaviour; pinning a new image or software version; or retracting/correcting an earlier claim. Also use when asked to record, document, or write up learnings, or when checking what is already known before starting an investigation.
---

# Knowledge capture

The knowledge base is a strictly conformant **OKF v0.2** bundle at
`/mnt/Models/knowledge` (override with `$KB_ROOT`). It is shared over NFS and
**has no version control** — conventions below are load-bearing.

`kb.py` sits next to the data: `python3 /mnt/Models/knowledge/kb.py`

## Before you start work: check what is known

Do this *before* investigating, not after. It routinely saves the whole
investigation.

```bash
kb.py query --tag <subsystem>          # ray, kernels, quantization, networking...
kb.py query --evidence-level refuted   # things already proven NOT to work
```

If a concept already answers your question, cite it. If it is `assumed` or
`refuted`, treat that as a starting hypothesis, not as settled fact.

## When to capture

Capture when any of these just happened. Each maps to a `type`:

| What just happened | `type` |
|---|---|
| Ran a benchmark / measured hardware | `method`, `platform`, or `recipe` |
| Hit an error with distinctive text | `signature` |
| **Tried something that did not work** | `compatibility-fact` + `evidence_level: refuted` |
| Eliminated a hypothesis | update the relevant `finding` |
| Found version-specific behaviour | `compatibility-fact` |
| Pinned a new image/version | `stack` |
| **Retracted or corrected a claim** | edit the concept — see *Retraction* below |

The two most-skipped are the bolded ones. Negative results feel like non-events
and retractions feel like something to move past quietly — they are the highest
value entries in the base, because they are what stops the next agent repeating
your dead end.

## How to capture

**1. Scaffold** — never start from a blank file:

```bash
KB_AGENT=<your-model-or-name> kb.py new <type> <kebab-slug> \
    --tags vllm,kernels --evidence measured
```

**2. Fill the TODOs.** Requirements that `validate` enforces:

- `description` — one sentence; it is what appears in every index
- `tags` — the facets someone will search by: software (`vllm`, `ray`, `cuda`),
  subsystem (`kernels`, `networking`, `memory`), hardware (`gb10`, `sm_121`),
  concern (`correctness`, `stability`)
- `evidence_level` — see below
- `sources` — **required** when `measured`; point at raw output, not at prose
- `applies_to` / `invalidated_by` — what this is bound to

**3. Validate and index:**

```bash
kb.py validate && kb.py index      # validate MUST be 0 errors
```

## `evidence_level` — the field that matters most

OKF's `status` is *lifecycle* (`draft`/`stable`/`deprecated`). `evidence_level` is
*epistemic*. **Never conflate them** — `validate` rejects a non-OKF `status`.

| Value | Use when |
|---|---|
| `measured` | You ran it. `sources` MUST link real output. |
| `derived` | Computed from measured facts. State the formula. |
| `reported` | Upstream docs/changelog/source claims it. Cite it. |
| `assumed` | Working assumption, unverified. Say so in the body. |
| `refuted` | You tested it; it does **not** hold. |

A document can be `status: stable` — reviewed, current, confidently written — while
its claim is `evidence_level: assumed`. That distinction exists because stating an
inference in the same voice as a measurement is the single most common way this
knowledge base would mislead someone.

**Do not write `measured` for something you reasoned out.** If you did not run it,
it is `derived` or `assumed`.

## Retraction discipline

When a claim turns out to be wrong:

- **Do not delete it.** Downgrade `evidence_level` (usually to `refuted`), rewrite
  the body to state what is actually true, and say what the earlier claim was.
- If it was superseded rather than wrong, set `status: deprecated` and link forward.
- Append the correction to `/log.md`.

A retracted claim that leaves a trace teaches; one that vanishes invites the same
mistake again.

## Working on the shared bundle (no git)

Full detail in [/methods/shared-bundle-on-nfs.md](/methods/shared-bundle-on-nfs.md).
The essentials:

- **One concept per file.** New knowledge = new file. Two agents adding concepts
  cannot collide; two agents editing the same concept will silently last-writer-win.
- **Write atomically** — temp file in the same directory, then `mv`. `kb.py` does
  this; do it by hand too. A torn write is unrecoverable here.
- **`log.md` is append-only.**
- **Do not hand-edit** `index.md` or `_views/` — run `kb.py index`.
- Re-read a concept immediately before editing someone else's.
- Do not trust "file not found" for ~60 s after a remote write; NFS attribute
  caching lags. Re-list the directory.

## When a version changes

```bash
kb.py todo --stack /stacks/<old-stack>.md
```

Lists every claim bound to that stack needing re-verification, `measured` first.
Re-test, then append to `verified` and add the new stack to `applies_to.stacks`.

**Carrying a claim forward without re-testing means downgrading it to `assumed`.**
Silent carry-forward is how a knowledge base becomes a liability.

## Quality bar

Before finishing, check your entry against these:

- Would this have saved *you* time this session if it already existed?
- Does a reader learn *why*, not just *what*?
- Is the error text **literal and complete** (signatures are matched by search)?
- Are the numbers accompanied by the conditions that produced them — warmup
  policy, concurrency, versions?
- If `measured`, can someone follow `sources` to the raw output?

If the answer to the first question is no, you may not need an entry. Do not pad
the base with restatements of upstream documentation.
