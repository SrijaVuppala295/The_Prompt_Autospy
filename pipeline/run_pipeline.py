"""
Part 3 — The Architect: Prompt Iteration Pipeline

WHAT IT DOES:
  Takes any system prompt + folder of transcripts and tells you
  how well that prompt performs — in one command.

  For each transcript:
    1. Fill customer variables into the prompt
    2. Feed borrower messages into LLM — LLM acts as the agent
    3. Score the simulated conversation using Part 1 rubric
    4. Save result

  Output: aggregate score, what worked, what didn't, breakdown

HOW TO COMPARE TWO PROMPTS:
  python pipeline/run_pipeline.py --prompt system-prompt.md --transcripts transcripts/
  → score: 54/100

  python pipeline/run_pipeline.py --prompt system-prompt-fixed.md --transcripts transcripts/
  → score: 60/100

  Higher score = better prompt. One command. No code changes needed.

BONUS — auto-suggest improvements:
  python pipeline/run_pipeline.py --prompt system-prompt.md --transcripts transcripts/ --suggest
"""

import os
import sys
import json
import time
import glob
import argparse
import requests
from datetime import datetime

try:
    from groq import Groq
except ImportError:
    print("ERROR: groq not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════
# API KEY POOL + ROTATION
# ═══════════════════════════════════════════════════════════════

def load_key_pool():
    pool = []
    for i in range(1, 16):
        key = os.getenv(f"GROQ_KEY_{i}")
        if key:
            pool.append({
                "provider":   "groq",
                "key":        key,
                "model":      "llama-3.3-70b-versatile",
                "label":      f"Groq-{i}",
                "rpm_limit":  25,
                "last_used":  0,
                "fail_count": 0,
            })
    for i in range(1, 11):
        key = os.getenv(f"OPENROUTER_KEY_{i}")
        if key:
            pool.append({
                "provider":   "openrouter",
                "key":        key,
                "model":      "meta-llama/llama-3.1-8b-instruct:free",
                "label":      f"OR-{i}",
                "rpm_limit":  15,
                "last_used":  0,
                "fail_count": 0,
            })
    if not pool:
        print("ERROR: No API keys found. Add GROQ_KEY_1=... to .env")
        sys.exit(1)
    print(f"Keys loaded: {[k['label'] for k in pool]}")
    return pool

KEY_POOL = load_key_pool()
_idx     = 0

def get_next_key():
    global _idx
    for _ in range(len(KEY_POOL)):
        k    = KEY_POOL[_idx]
        _idx = (_idx + 1) % len(KEY_POOL)
        if k["fail_count"] >= 3:
            continue
        wait = (60 / k["rpm_limit"]) - (time.time() - k["last_used"])
        if wait > 0:
            print(f"  [{k['label']}] waiting {wait:.1f}s")
            time.sleep(wait)
        return k
    print("  All keys cooling — waiting 30s")
    time.sleep(30)
    for k in KEY_POOL: k["fail_count"] = 0
    return KEY_POOL[0]

def call_llm(messages, max_tokens=500, json_mode=False):
    for _ in range(len(KEY_POOL) * 2):
        k = get_next_key()
        try:
            k["last_used"] = time.time()
            print(f"  Using: {k['label']}")
            if k["provider"] == "groq":
                kwargs = {
                    "model":       k["model"],
                    "messages":    messages,
                    "temperature": 0,
                    "max_tokens":  max_tokens,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                r = Groq(api_key=k["key"]).chat.completions.create(**kwargs)
                k["fail_count"] = 0
                return r.choices[0].message.content.strip()
            else:
                r = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {k['key']}",
                             "Content-Type": "application/json",
                             "HTTP-Referer": "https://github.com/prompt-autopsy"},
                    json={"model": k["model"], "messages": messages,
                          "temperature": 0, "max_tokens": max_tokens},
                    timeout=60,
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                if content is None:
                    raise ValueError("OpenRouter returned None")
                k["fail_count"] = 0
                return content.strip()
        except Exception as e:
            k["fail_count"] += 1
            err = str(e)
            if "404" in err:
                print(f"  [{k['label']}] model not found — skipping")
                k["fail_count"] = 99
            elif "429" in err or "rate_limit" in err:
                print(f"  [{k['label']}] rate limit — waiting 20s")
                time.sleep(20)
            elif "413" in err or "too large" in err:
                print(f"  [{k['label']}] too large — rotating")
                time.sleep(3)
            elif "401" in err or "403" in err:
                print(f"  [{k['label']}] auth error — skipping")
                k["fail_count"] = 99
            else:
                print(f"  [{k['label']}] error: {err[:80]}")
                time.sleep(5)
    return None


# ═══════════════════════════════════════════════════════════════
# FILE HELPERS
# ═══════════════════════════════════════════════════════════════

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def fill_variables(prompt, customer):
    """Replace {{variable}} placeholders with real customer values."""
    vals = {
        "{{customer_name}}":     customer.get("name", "Customer"),
        "{{pending_amount}}":    customer.get("pending_amount", "fifty thousand"),
        "{{tos}}":               customer.get("pending_amount", "fifty thousand"),
        "{{pos}}":               customer.get("closure_amount", "thirty five thousand"),
        "{{settlement_amount}}": customer.get("settlement_amount", "twenty five thousand"),
        "{{dpd}}":               customer.get("dpd", "180"),
        "{{due_date}}":          "as soon as possible",
        "{{today_date}}":        "16/03/2026",
        "{{today_day}}":         "Monday",
        "{{loan_id}}":           "DEMOLOAN001",
        "{{lender_name}}":       "DemoLender",
    }
    for k, v in vals.items():
        prompt = prompt.replace(k, str(v) if v else "N/A")
    return prompt

def get_customer_msgs(data):
    """Extract customer messages from transcript."""
    return [
        t["text"] for t in data.get("transcript", [])
        if t["speaker"] == "customer" and t["text"].strip()
    ]

def format_transcript(transcript):
    return "\n".join(
        f"[{t['speaker'].upper()}]: {t['text']}"
        for t in transcript
    )


# ═══════════════════════════════════════════════════════════════
# STEP 1 — SIMULATE
#
# Takes borrower messages from a real transcript and feeds them
# one by one into the LLM using the given system prompt.
# The LLM acts as the agent — this reveals how the prompt behaves
# on real borrower inputs.
#
# Role header prepended to prevent LLM from discussing the prompt
# instead of acting as the agent.
# ═══════════════════════════════════════════════════════════════

def simulate_call(system_prompt, customer_messages, max_turns=6):
    history      = []
    conversation = []

    role_header = (
        "You are a debt collection agent named Alex. "
        "You are ON A LIVE PHONE CALL right now. "
        "Respond ONLY as Alex speaking to the borrower. "
        "Do NOT discuss instructions or system messages. "
        "Keep responses to 1-2 sentences maximum.\n\n"
    )

    # 5000 chars covers all critical fixes in the fixed prompt
    combined = role_header + system_prompt[:5000]

    for msg in customer_messages[:max_turns]:
        history.append({"role": "user", "content": msg})
        conversation.append({"speaker": "customer", "text": msg})

        reply = call_llm(
            [{"role": "system", "content": combined}, *history],
            max_tokens=150,
            json_mode=False,
        )
        reply = reply or "[No response]"

        history.append({"role": "assistant", "content": reply})
        conversation.append({"speaker": "agent", "text": reply})
        time.sleep(2)

    return conversation


# ═══════════════════════════════════════════════════════════════
# STEP 2 — SCORE
#
# Scores the simulated conversation using the same 7-category
# rubric as Part 1 evaluator. temperature=0 for consistency.
# ═══════════════════════════════════════════════════════════════

SCORING_RUBRIC = """
You are a strict QA auditor evaluating an AI debt collection call.
Score the agent ONLY from transcript evidence. Do NOT use labels.

RUBRIC (Total = 100):

1. Identity & Opening (0-10)
   Did agent introduce themselves and confirm correct borrower?

2. Empathy & Tone (0-20)
   Was tone respectful? Did agent acknowledge hardship when mentioned?
   Urgency is OK for willing-but-delaying borrowers.
   Urgency on grieving or hardship customers = heavy deduction.

3. Language Handling (0-15)
   Did agent switch language when customer spoke Hindi/Tamil/etc?
   Did agent stay in that language consistently?

4. Information Accuracy (0-15)
   Were loan amounts consistent? Did agent avoid fabricated figures?

5. Dispute Handling (0-15)
   If customer claimed already paid or disputed loan:
   did agent acknowledge and provide clear next step (email, escalation)?

6. Negotiation Quality (0-15)
   Did agent explore borrower's situation?
   Did agent try different angles before giving up?

7. Call Resolution (0-10)
   Did call end with a clear next step?
   If connection dropped — did agent recover or just say Goodbye?

VERDICT: score >= 60 = good, score < 60 = bad

Return ONLY valid JSON:
{
  "score": <0-100>,
  "verdict": "<good or bad>",
  "reasoning": "<2-3 sentences>",
  "worst_moment": "<worst agent message and why>",
  "best_moment": "<best agent message>",
  "breakdown": {
    "identity_opening":     <0-10>,
    "empathy_tone":         <0-20>,
    "language_handling":    <0-15>,
    "information_accuracy": <0-15>,
    "dispute_handling":     <0-15>,
    "negotiation_quality":  <0-15>,
    "call_resolution":      <0-10>
  }
}
"""

def score_conversation(conversation, call_id, customer_name):
    transcript_text = format_transcript(conversation)
    prompt = (
        f"{SCORING_RUBRIC}\n\n"
        f"CALL: {call_id} | Customer: {customer_name}\n"
        f"Turns: {len(conversation)}\n\n"
        f"TRANSCRIPT:\n{transcript_text[:3000]}\n\n"
        f"Return ONLY valid JSON."
    )
    raw = call_llm(
        [
            {"role": "system", "content": "You are a strict QA evaluator. Return ONLY valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=500,
        json_mode=True,
    )
    if not raw:
        return {"score": 0, "verdict": "bad",
                "reasoning": "API failed", "worst_moment": "",
                "best_moment": "", "breakdown": {}}
    try:
        clean = raw.strip().strip("```json").strip("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {"score": 0, "verdict": "bad",
                "reasoning": f"JSON parse error: {raw[:80]}",
                "worst_moment": "", "best_moment": "", "breakdown": {}}


# ═══════════════════════════════════════════════════════════════
# STEP 3 — REPORT
# ═══════════════════════════════════════════════════════════════

def generate_report(prompt_name, all_results, output_dir):
    scores   = [r["score_result"].get("score", 0) for r in all_results]
    verdicts = [r["score_result"].get("verdict", "bad") for r in all_results]
    avg      = sum(scores) / len(scores) if scores else 0
    good     = verdicts.count("good")
    bad      = verdicts.count("bad")

    lines = []
    lines.append(f"{'='*65}")
    lines.append(f"PIPELINE REPORT")
    lines.append(f"Prompt  : {prompt_name}")
    lines.append(f"Run at  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"{'='*65}")
    lines.append(f"")
    lines.append(f"AGGREGATE SCORE : {avg:.1f}/100")
    lines.append(f"Good: {good}/{len(all_results)}   Bad: {bad}/{len(all_results)}")
    lines.append(f"")
    lines.append(f"{'─'*65}")
    lines.append(f"{'Call':<12} {'Customer':<20} {'Score':<8} {'Verdict':<8} Disposition")
    lines.append(f"{'-'*65}")

    for r in all_results:
        sr = r["score_result"]
        lines.append(
            f"{r['call_id']:<12} {r['customer_name']:<20} "
            f"{sr.get('score',0):<8} {sr.get('verdict','?').upper():<8} "
            f"{r['disposition']}"
        )

    lines.append(f"{'─'*65}")
    lines.append("")

    lines.append("WHAT WORKED:")
    good_calls = [r for r in all_results if r["score_result"].get("verdict") == "good"]
    for r in good_calls:
        sr = r["score_result"]
        lines.append(f"  ✅ {r['call_id']} ({r['disposition']}) — {sr.get('score')}/100")
        lines.append(f"     {sr.get('reasoning','')[:100]}")
    if not good_calls:
        lines.append("  None")
    lines.append("")

    lines.append("WHAT DIDN'T WORK:")
    bad_calls = [r for r in all_results if r["score_result"].get("verdict") == "bad"]
    for r in bad_calls:
        sr = r["score_result"]
        lines.append(f"  ❌ {r['call_id']} ({r['disposition']}) — {sr.get('score')}/100")
        lines.append(f"     {sr.get('reasoning','')[:100]}")
        if sr.get("worst_moment"):
            lines.append(f"     Worst: {sr['worst_moment'][:90]}")
    if not bad_calls:
        lines.append("  None")
    lines.append("")

    lines.append("SCORE BREAKDOWN (averages across all calls):")
    maxes = {
        "identity_opening": 10, "empathy_tone": 20, "language_handling": 15,
        "information_accuracy": 15, "dispute_handling": 15,
        "negotiation_quality": 15, "call_resolution": 10,
    }
    for key, mx in maxes.items():
        vals    = [r["score_result"].get("breakdown", {}).get(key, 0) for r in all_results]
        avg_key = sum(vals) / len(vals) if vals else 0
        bar     = "█" * int((avg_key / mx) * 20) + "░" * (20 - int((avg_key / mx) * 20))
        lines.append(f"  {key:<25} {bar} {avg_key:.1f}/{mx}")

    lines.append(f"{'='*65}")
    report = "\n".join(lines)
    print(report)

    with open(os.path.join(output_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write(report)
    save_json({
        "prompt":          prompt_name,
        "run_at":          datetime.now().isoformat(),
        "aggregate_score": round(avg, 1),
        "good":            good,
        "bad":             bad,
        "results":         all_results,
    }, os.path.join(output_dir, "report.json"))

    return avg


# ═══════════════════════════════════════════════════════════════
# BONUS — SUGGEST IMPROVEMENTS
# ═══════════════════════════════════════════════════════════════

def suggest_improvements(all_results, prompt_text, output_dir):
    bad_calls = [r for r in all_results if r["score_result"].get("verdict") == "bad"]
    if not bad_calls:
        print("All calls good — no suggestions needed.")
        return

    issues = "\n".join(
        f"- {r['call_id']} ({r['disposition']}): {r['score_result'].get('reasoning','')[:100]}"
        for r in bad_calls
    )
    prompt = (
        f"You are a prompt engineer reviewing a broken AI debt collection system prompt.\n\n"
        f"Failures found:\n{issues}\n\n"
        f"Current prompt (first 1500 chars):\n{prompt_text[:1500]}\n\n"
        f"Suggest 3 specific, concrete changes. "
        f"Quote exact text to change and what to replace it with. Be brief."
    )
    print("\nGenerating improvement suggestions...")
    result = call_llm(
        [
            {"role": "system", "content": "You are a prompt engineer. Be specific and brief."},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=600,
    )
    if result:
        print(f"\n{'='*65}\nSUGGESTED IMPROVEMENTS:\n{'='*65}")
        print(result)
        with open(os.path.join(output_dir, "suggestions.txt"), "w", encoding="utf-8") as f:
            f.write(result)


# ═══════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════

def run_pipeline(prompt_path, transcripts_folder, output_dir, suggest=False):
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(prompt_path):
        print(f"ERROR: Prompt file not found: {prompt_path}")
        sys.exit(1)

    prompt_text = load_text(prompt_path)
    prompt_name = os.path.basename(prompt_path)

    files = sorted([
        f for f in glob.glob(os.path.join(transcripts_folder, "*.json"))
        if not os.path.basename(f).startswith("_")
    ])

    if not files:
        print(f"ERROR: No JSON files found in {transcripts_folder}")
        sys.exit(1)

    print(f"\nPrompt      : {prompt_name}")
    print(f"Transcripts : {len(files)} files")
    print(f"Keys        : {len(KEY_POOL)} loaded")
    print(f"Output      : {output_dir}\n")

    all_results = []

    for i, filepath in enumerate(files):
        data = load_json(filepath)
        if isinstance(data, list):
            continue

        call_id     = data.get("call_id", "unknown")
        customer    = data.get("customer", {})
        disposition = data.get("disposition", "unknown")

        print(f"\n[{i+1}/{len(files)}] {call_id} | {customer.get('name')} | {disposition}")

        filled_prompt = fill_variables(prompt_text, customer)
        msgs          = get_customer_msgs(data)

        # Step 1: Simulate — feed borrower messages into LLM with this prompt
        print(f"  Simulating ({min(6, len(msgs))} turns)...")
        simulated = simulate_call(filled_prompt, msgs, max_turns=6)
        time.sleep(2)

        # Step 2: Score the simulated conversation
        print(f"  Scoring...")
        score_result = score_conversation(
            simulated, call_id, customer.get("name", "?")
        )
        time.sleep(2)

        print(f"  Score: {score_result.get('score')}/100 → {score_result.get('verdict','?').upper()}")

        result = {
            "call_id":       call_id,
            "customer_name": customer.get("name", "?"),
            "disposition":   disposition,
            "simulated":     simulated,
            "score_result":  score_result,
        }
        all_results.append(result)
        save_json(result, os.path.join(output_dir, f"{call_id}_result.json"))

        if i < len(files) - 1:
            delay = max(3, 8 - len(KEY_POOL))
            print(f"  Waiting {delay}s...")
            time.sleep(delay)

    print(f"\n{'='*65}\nGENERATING REPORT\n{'='*65}\n")
    generate_report(prompt_name, all_results, output_dir)

    if suggest:
        suggest_improvements(all_results, prompt_text, output_dir)

    print(f"\nDone. Results saved to: {output_dir}/")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test any system prompt against all transcripts in one command"
    )
    parser.add_argument("--prompt",      required=True,
                        help="Path to system prompt file (.md)")
    parser.add_argument("--transcripts", required=True,
                        help="Path to folder containing transcript JSON files")
    parser.add_argument("--output",      default=None,
                        help="Output folder (auto-named if not set)")
    parser.add_argument("--suggest",     action="store_true",
                        help="Bonus: auto-suggest prompt improvements from bad calls")
    args = parser.parse_args()

    if args.output is None:
        name      = os.path.splitext(os.path.basename(args.prompt))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir   = os.path.join("results", f"pipeline_{name}_{timestamp}")
    else:
        out_dir = args.output

    run_pipeline(args.prompt, args.transcripts, out_dir, args.suggest)