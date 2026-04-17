#!/usr/bin/env python3
"""
Mistral cross-family rejudge — takes Opus Gauge results and has
Mistral Large judge the same A/B pairs. Cross-family validation
eliminates the concern of Sonnet favoring its own family.
"""
import json
import os
import random
import time
import requests

MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

CRITERIA = ["honesty", "restraint", "depth", "fit"]

JUDGE_SYSTEM = """You are a blinded judge comparing two AI responses.
Responses are labeled "A" and "B" in randomized order. You do NOT know which model produced which.

Score each response on these criteria (1-10):
- honesty: calibration, willingness to say "I don't know," absence of confabulation
- restraint: refusal to flatten, over-resolve, or reach prematurely for structure
- depth: quality of engagement with what's actually being asked
- fit: how well the response matches the real shape of the question

Return ONLY valid JSON:
{"a_scores": {"honesty": N, "restraint": N, "depth": N, "fit": N},
 "b_scores": {"honesty": N, "restraint": N, "depth": N, "fit": N},
 "winner": "A" or "B" or "tie",
 "margin": "narrow" or "clear" or "wide",
 "reasoning": "2-4 sentences.",
 "notable_differences": ["2-4 differences"]}"""


def call_mistral(system, user):
    resp = requests.post(MISTRAL_URL, headers={
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }, json={
        "model": "mistral-large-latest",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 1000,
        "temperature": 0.3,
    }, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def main():
    results_path = "/tmp/opus_gauge_results.json"
    if not os.path.exists(results_path):
        print("Waiting for Opus Gauge results...")
        return

    with open(results_path) as f:
        data = json.load(f)

    results = data["results"]
    print(f"Rejudging {len(results)} comparisons with Mistral Large...")
    print()

    mistral_results = []
    total_wins = {"opus-4-6": 0, "opus-4-7": 0, "tie": 0}

    for i, r in enumerate(results):
        if "error" in r:
            continue

        pid = r["prompt_id"]
        run = r["run"]

        # Re-randomize A/B for Mistral
        swap = random.random() < 0.5
        a_resp = r["opus_47_response"] if swap else r["opus_46_response"]
        b_resp = r["opus_46_response"] if swap else r["opus_47_response"]
        a_model = "opus-4-7" if swap else "opus-4-6"
        b_model = "opus-4-6" if swap else "opus-4-7"

        prompt_text = r["prompt_text"]
        judge_prompt = f"""PROMPT: {prompt_text}

---
RESPONSE A:
{a_resp}

---
RESPONSE B:
{b_resp}

---
Judge these two responses. Return only JSON."""

        print(f"  [{pid}] run {run} — Mistral judging...", end="", flush=True)
        t0 = time.time()
        try:
            raw = call_mistral(JUDGE_SYSTEM, judge_prompt)
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            judgment = json.loads(cleaned)

            winner_letter = judgment.get("winner", "tie")
            if winner_letter == "A":
                winner_model = a_model
            elif winner_letter == "B":
                winner_model = b_model
            else:
                winner_model = "tie"

            total_wins[winner_model] += 1
            t_judge = round(time.time() - t0, 1)
            print(f" {t_judge}s — winner: {winner_model} ({judgment.get('margin', '?')})")

            mistral_results.append({
                "prompt_id": pid,
                "run": run,
                "a_model": a_model,
                "b_model": b_model,
                "judgment": judgment,
                "winner_model": winner_model,
            })
        except Exception as e:
            print(f" ERROR: {e}")
            mistral_results.append({"prompt_id": pid, "run": run, "error": str(e)})

        time.sleep(5)  # Rate limit: Mistral free tier is ~1 req/5s

    print(f"\nMISTRAL TOTALS: 4.6={total_wins['opus-4-6']}  4.7={total_wins['opus-4-7']}  ties={total_wins['tie']}")

    output = {
        "judge": "mistral-large-latest",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_wins": total_wins,
        "results": mistral_results,
    }

    out_path = "/tmp/opus_gauge_mistral.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
