#!/usr/bin/env python3
"""Greedy-decode reference with HF transformers, same output format as
greedy_capture.py so compare_captures.py can diff vLLM against it.

Run inside a container with transformers + the model available, e.g.:
  docker run --rm --gpus all --ipc=host \
    -v ~/.cache/huggingface:/root/.cache/huggingface \
    -v $PWD:/w -w /w --entrypoint python3 \
    vllm/vllm-openai:v0.26.0 \
    validate/hf_reference.py --model Qwen/Qwen3-1.7B --out results/ref.json

Note: HF loads the model dense (no paged attention); use small models or
be patient. Logprob is the chosen token's log-softmax at each step.
"""
import argparse, json, sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompts", default="validate/prompts.txt")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--dtype", default="bfloat16")
    a = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForCausalLM.from_pretrained(
        a.model, dtype=getattr(torch, a.dtype), device_map="cuda"
    )
    model.eval()

    prompts = [l.rstrip("\n") for l in open(a.prompts) if l.strip()]
    results = []
    for i, p in enumerate(prompts):
        ids = tok(p, return_tensors="pt").input_ids.to("cuda")
        toks, lps = [], []
        with torch.no_grad():
            cur = ids
            past = None
            for _ in range(a.max_tokens):
                out = model(cur if past is None else cur[:, -1:], past_key_values=past, use_cache=True)
                past = out.past_key_values
                logits = out.logits[0, -1]
                lp = torch.log_softmax(logits.float(), dim=-1)
                nxt = int(torch.argmax(lp))
                toks.append(nxt)
                lps.append(float(lp[nxt]))
                cur = torch.cat([cur, torch.tensor([[nxt]], device="cuda")], dim=1)
                if nxt == tok.eos_token_id:
                    break
        text = tok.decode(toks)
        # tokens as strings, matching vLLM's logprobs.tokens representation
        tok_strs = [tok.convert_ids_to_tokens(t) for t in toks]
        results.append({"prompt": p, "text": text, "tokens": tok_strs, "logprobs": lps,
                        "finish_reason": "stop" if toks and toks[-1] == tok.eos_token_id else "length"})
        print(f"  [{i+1}/{len(prompts)}] {p[:40]!r} -> {text[:40]!r}", file=sys.stderr)

    json.dump(results, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}")

if __name__ == "__main__":
    main()
