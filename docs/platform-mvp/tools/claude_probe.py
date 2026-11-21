"""Coordinator's probe (PMVP-20 rework 2): does Claude answer in the statement form and quote word for word?
Eight INVENTED cases from core_llm_probe.py; the key is read from a file and never printed. Not product code."""
import json, sys, time
from pathlib import Path

import anthropic

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core_llm_probe as probe  # SYSTEM, CASES, user_message, judge (the coordinator's reference check)

KEY_FILE = Path(r"E:\Knovas\.secrets\anthropic_api_key.txt.txt")
MODEL = "claude-opus-5-5"

client = anthropic.Anthropic(api_key=KEY_FILE.read_text(encoding="utf-8-sig").strip())
rows = []
for name, passages, question in probe.CASES:
    started = time.monotonic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=probe.SYSTEM,
            messages=[{"role": "user", "content": probe.user_message(passages, question)}],
        )
    except anthropic.AuthenticationError:
        print("the key was refused (401); stopping"); break
    except anthropic.PermissionDeniedError:
        print("the key may not use this model (403); stopping"); break
    except anthropic.RateLimitError:
        print("rate limited (429); stopping"); break
    except anthropic.APIStatusError as e:
        print(f"{name}: HTTP {e.status_code}; stopping"); break
    except anthropic.APIConnectionError:
        print("no connection to the API; stopping"); break
    seconds = time.monotonic() - started
    if response.stop_reason == "refusal":
        print(f"{name}: refused"); rows.append({"case": name, "refused": True}); continue
    answer = "".join(b.text for b in response.content if b.type == "text")
    kept, reasons, said_no = probe.judge(answer, passages)
    expected_no = name.startswith("negativ")
    rows.append({"case": name, "seconds": round(seconds, 1), "model": response.model, "stop": response.stop_reason,
                 "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
                 "kept": kept, "removed": reasons, "said_no_answer": said_no, "answer": answer})
    verdict = ("ok" if said_no and not kept else "WRONG") if expected_no else ("ok" if kept and not reasons else ("partly" if kept else "WRONG"))
    print(f"{name:18} {seconds:5.1f}s kept={len(kept)} removed={reasons} no_answer={said_no} -> {verdict}")
(HERE / "claude_probe_out.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
tin = sum(r.get("input_tokens", 0) for r in rows); tout = sum(r.get("output_tokens", 0) for r in rows)
print(f"{len(rows)} answers; tokens in/out {tin}/{tout}; cost about USD {tin*4/1e6 + tout*20/1e6:.3f}")
