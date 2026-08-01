# DEPRECATED — do not use in default builds

**Status:** obsolete maintenance trap. Keep only as archaeology.

| Claim | Reality |
|---|---|
| Purpose | A/B overlay of DSpark draft-path opts on PR-41834 |
| Perf | **Neutral / no win** under corrected metering (see VALIDATION.md) |
| Upstream | Same draft-head deltas absorbed by **vllm #49731** (merged to main) |
| Flagship pin | `vllm-gb10:pr41834-d64074e6f` — **does not** use this overlay |
| Default path | Root `Dockerfile` and `docs/BUILD.md` flagship recipe **never** reference this tree |

## Do not

- Build this by accident as “the” flagship image  
- Point `models/deepseek-v4-flash.conf` `IMAGE=` at a `*-dspark-opt-*` tag for production  
- Expand this overlay further

## If you rebuild history

```bash
# intentional A/B only — not ship default
docker build -t vllm-gb10:pr41834-dspark-opt-v1-HISTORICAL \
  -f patches/pr41834-dspark-opt/Dockerfile .
```

Prefer next flagship pin that already includes #49731 over this tree.

## Files

| File | Role |
|---|---|
| `Dockerfile` | Overlay on `vllm-gb10:pr41834-d64074e6f` |
| `dspark.py`, `speculator.py`, `envs.py` | Pure-Python port of fork draft-path opts |

History of experiments remains in git; removal from the tree is optional later once the next pin lands.
