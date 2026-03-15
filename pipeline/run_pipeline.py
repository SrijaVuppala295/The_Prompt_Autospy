"""
Part 3 - The Architect: Prompt Iteration Pipeline

Usage:
  python run_pipeline.py --prompt system-prompt.md --transcripts transcripts/
  python run_pipeline.py --prompt system-prompt-fixed.md --transcripts transcripts/
  python run_pipeline.py --prompt system-prompt-fixed.md --transcripts transcripts/ --suggest

What it does:
  1. Takes a system prompt + folder of transcripts
  2. Simulates the agent on each transcript using the given prompt
  3. Scores each conversation using Part 1 evaluator criteria
  4. Outputs a report: what worked, what didn't, aggregate score

Compare two prompts in two commands:
  python run_pipeline.py --prompt system-prompt.md --transcripts transcripts/
  python run_pipeline.py --prompt system-prompt-fixed.md --transcripts transcripts/
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
    print("ERROR: groq not installed. Run: pip install groq")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── API key rotation ──────────────────────────────────────────────────────────

def load_key_pool():
    pool = []
    for i in range(1, 11):
        key = os.getenv(f"GROQ_KEY_{i}")
        if key:
            pool.append({
                "provider": "groq", "key": key,
                "model": "llama-3.3-70b-versatile",
                "label": f"Groq-{i}", "rpm_limit": 30,
                "last_used": 0, "fail_count": 0,
            })
    for i in range(1, 11):
        key = os.getenv(f"OPENROUTER_KEY_{i}")
        if key:
            pool.append({
                "provider": "openrouter", "key": key,
                "model": "meta-llama/llama-3.3-70b-instruct:free",
                "label": f"OpenRouter-{i}", "rpm_limit": 20,
                "last_used": 0, "fail_count": 0,
            })
    if not pool:
        print("ERROR: No API keys found. Add GROQ_KEY_1=... to .env")
        sys.exit(1)
    return pool

KEY_POOL = load_key_pool()
_key_idx = 0

def get_next_key():
    global _key_idx
    for _ in range(len(KEY_POOL)):
        k = KEY_POOL[_key_idx]
        _key_idx = (_key_idx + 1) % len(KEY_POOL)
        if k["fail_count"] >= 3:
            continue
        gap = 60 / k["rpm_limit"]
        wait = gap - (time.time() - k["last_used"])
        if wait > 0:
            time.sleep(wait)
        return k
    time.sleep(60)
    for k in KEY_POOL: k["fail_count"] = 0
    return KEY_POOL[0]

def call_llm(messages, max_tokens=500):
    for _ in range(len(KEY_POOL) * 2):
        k = get_next_key()
        try:
            k["last_used"] = time.time()
            if k["provider"] == "groq":
                client = Groq(api_key=k["key"])
                r = client.chat.completions.create(
                    model=k["model"], messages=messages,
                    temperature=0.3, max_tokens=max_tokens)
                k["fail_count"] = 0
                return r.choices[0].message.content.strip()
            elif k["provider"] == "openrouter":
                r = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {k['key']}",
                             "Content-Type": "application/json"},
                    json={"model": k["model"], "messages": messages,
                          "temperature": 0.3, "max_tokens": max_tokens},
                    timeout=60)
                r.raise_for_status()
                k["fail_count"] = 0
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            k["fail_count"] += 1
            err = str(e)
            if "413" in err or "rate_limit" in err or "too large" in err: time.sleep(5)
            elif "429" in err: time.sleep(15)
            else: time.sleep(3)
    return None


# ── File helpers ──────────────────────────────────────────────────────────────

def load_json(path):
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def load_text(path):
    with open(path, "r", encoding="utf-8") as f: return f.read()

def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def fill_variables(prompt, customer):
    vals = {
        "{{customer_name}}":     customer.get("name", "Customer"),
        "{{pending_amount}}":    customer.get("pending_amount", "fifty thousand"),
        "{{tos}}":               customer.get("pending_amount", "fifty thousand"),
        "{{pos}}":               customer.get("closure_amount", "thirty five thousand"),
        "{{settlement_amount}}": customer.get("settlement_amount", "twenty five thousand"),
        "{{dpd}}":               customer.get("dpd", "180"),
        "{{due_date}}":          "as soon as possible",
        "{{today_date}}":        "15/03/2026",
        "{{today_day}}":         "Sunday",
        "{{loan_id}}":           "DEMOLOAN001",
        "{{lender_name}}":       "DemoLender",
    }
    for k, v in vals.items():
        prompt = prompt.replace(k, str(v) if v else "N/A")
    return prompt

def get_customer_msgs(data):
    return [t["text"] for t in data.get("transcript", [])
            if t["speaker"] == "customer" and t["text"].strip()]


# ── Step 1: Simulate ──────────────────────────────────────────────────────────

def simulate_call(system_prompt, customer_messages, max_turns=6):
    """
    Core of the pipeline.
    Takes borrower messages from a real transcript,
    feeds them one by one into the LLM using the given system prompt,
    and collects the agent's responses.

    This tests HOW the prompt handles real borrower inputs.
    The LLM acts as the agent — its responses reveal prompt quality.

    max_turns=6 is enough to see:
    - How the agent opens (turns 1-2)
    - How it handles the core issue (turns 3-5)
    - How it starts to resolve (turn 6)
    """
    history      = []
    conversation = []

    # Truncate system prompt to avoid token limits
    # Keep first 2000 chars — enough for the agent's core instructions
    system_prompt = system_prompt[:2000]

    for msg in customer_messages[:max_turns]:
        history.append({"role": "user", "content": msg})
        conversation.append({"speaker": "customer", "text": msg})

        reply = call_llm(
            [{"role": "system", "content": system_prompt}, *history],
            max_tokens=150   # short responses only — agent should be concise
        )
        reply = reply or "[No response]"

        history.append({"role": "assistant", "content": reply})
        conversation.append({"speaker": "agent", "text": reply})

        time.sleep(2)  # wait between turns to avoid rate limits

    return conversation


# ── Step 2: Score ─────────────────────────────────────────────────────────────

SCORING_CRITERIA = """
You are a strict QA auditor for AI debt-collection calls.
Score the agent's behaviour based ONLY on the transcript.
Do NOT rely on the disposition label.

RUBRIC (Total = 100):
1. Identity & Opening     (0-10)  — introduced clearly, confirmed right person
2. Empathy & Tone         (0-20)  — acknowledged hardship, calm non-threatening tone
3. Language Handling      (0-15)  — switched to customer's language, stayed consistent
4. Information Accuracy   (0-15)  — amounts consistent, no contradictions
5. Dispute Handling       (0-15)  — acknowledged disputes, gave escalation path
6. Negotiation Quality    (0-15)  — explored options, understood borrower situation
7. Call Resolution        (0-10)  — clear next step at end of call

SPECIAL CASES:
- WRONG_NUMBER + clean exit = score 70-80 (good)
- ALREADY_PAID + agent loops = score low (bad)
- Very short call + no commitment = score low (bad)
- Callback treated as cold call = deduct 15pts

VERDICT: score >= 60 = good, score < 60 = bad

Return ONLY valid JSON:
{
  "score": <0-100>,
  "verdict": "<good or bad>",
  "breakdown": {
    "identity_opening": <0-10>,
    "empathy_tone": <0-20>,
    "language_handling": <0-15>,
    "information_accuracy": <0-15>,
    "dispute_handling": <0-15>,
    "negotiation_quality": <0-15>,
    "call_resolution": <0-10>
  },
  "worst_moment": "<worst agent message and why>",
  "best_moment": "<best agent message>",
  "reasoning": "<2-3 sentences>"
}
"""

def score_conversation(conversation, call_id, customer_name, disposition):
    transcript_text = "\n".join(
        f"[{t['speaker'].upper()}]: {t['text']}" for t in conversation)
    prompt = (
        f"{SCORING_CRITERIA}\n\n"
        f"CALL: {call_id} | Customer: {customer_name} | Disposition: {disposition}\n"
        f"Turns: {len(conversation)}\n\n"
        f"TRANSCRIPT:\n{transcript_text[:3500]}\n\n"
        f"Respond ONLY in valid JSON."
    )
    raw = call_llm(
        [{"role": "system", "content": "You are a strict QA evaluator. JSON only."},
         {"role": "user",   "content": prompt}],
        max_tokens=500)
    if not raw:
        return {"score": 0, "verdict": "bad", "reasoning": "API failed",
                "breakdown": {}, "worst_moment": "", "best_moment": ""}
    try:
        clean = raw.strip().strip("```json").strip("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {"score": 0, "verdict": "bad", "reasoning": f"Parse failed: {raw[:80]}",
                "breakdown": {}, "worst_moment": "", "best_moment": ""}


# ── Step 3: Report ────────────────────────────────────────────────────────────

def generate_report(prompt_name, all_results, output_dir):
    scores   = [r["score_result"].get("score", 0) for r in all_results]
    verdicts = [r["score_result"].get("verdict", "bad") for r in all_results]
    avg      = sum(scores) / len(scores) if scores else 0
    good     = verdicts.count("good")
    bad      = verdicts.count("bad")

    lines = []
    lines.append(f"{'='*65}")
    lines.append(f"PIPELINE REPORT")
    lines.append(f"Prompt : {prompt_name}")
    lines.append(f"Run at : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"{'='*65}")
    lines.append(f"")
    lines.append(f"AGGREGATE SCORE : {avg:.1f}/100")
    lines.append(f"Good: {good}/10   Bad: {bad}/10")
    lines.append(f"")
    lines.append(f"{'─'*65}")
    lines.append(f"{'Call':<12} {'Customer':<20} {'Score':<8} {'Verdict':<8} {'Disposition'}")
    lines.append(f"{'-'*65}")
    for r in all_results:
        sr = r["score_result"]
        lines.append(f"{r['call_id']:<12} {r['customer_name']:<20} "
                     f"{sr.get('score',0):<8} {sr.get('verdict','?').upper():<8} "
                     f"{r['disposition']}")
    lines.append(f"{'─'*65}")
    lines.append("")

    lines.append("WHAT WORKED:")
    good_calls = [r for r in all_results if r["score_result"].get("verdict") == "good"]
    for r in good_calls:
        sr = r["score_result"]
        lines.append(f"  ✅ {r['call_id']} — score {sr.get('score')}: {sr.get('reasoning','')[:90]}")
    if not good_calls: lines.append("  None")
    lines.append("")

    lines.append("WHAT DIDN'T WORK:")
    bad_calls = [r for r in all_results if r["score_result"].get("verdict") == "bad"]
    for r in bad_calls:
        sr = r["score_result"]
        lines.append(f"  ❌ {r['call_id']} — score {sr.get('score')}: {sr.get('reasoning','')[:90]}")
        if sr.get("worst_moment"):
            lines.append(f"     Worst: {sr['worst_moment'][:90]}")
    if not bad_calls: lines.append("  None")
    lines.append("")

    lines.append("SCORE BREAKDOWN (averages):")
    maxes = {"identity_opening":10,"empathy_tone":20,"language_handling":15,
             "information_accuracy":15,"dispute_handling":15,
             "negotiation_quality":15,"call_resolution":10}
    for key, mx in maxes.items():
        vals    = [r["score_result"].get("breakdown",{}).get(key,0) for r in all_results]
        avg_key = sum(vals)/len(vals) if vals else 0
        bar     = "█" * int((avg_key/mx)*20) + "░" * (20 - int((avg_key/mx)*20))
        lines.append(f"  {key:<25} {bar} {avg_key:.1f}/{mx}")

    lines.append(f"{'='*65}")
    report = "\n".join(lines)
    print(report)

    with open(os.path.join(output_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write(report)
    save_json({
        "prompt": prompt_name,
        "run_at": datetime.now().isoformat(),
        "aggregate_score": round(avg, 1),
        "good": good, "bad": bad,
        "results": all_results,
    }, os.path.join(output_dir, "report.json"))

    return avg


# ── Bonus: suggest improvements ───────────────────────────────────────────────

def suggest_improvements(all_results, prompt_text, output_dir):
    bad_calls = [r for r in all_results if r["score_result"].get("verdict") == "bad"]
    if not bad_calls:
        print("All calls good — no suggestions needed.")
        return

    issues = "\n".join(
        f"- {r['call_id']} ({r['disposition']}): {r['score_result'].get('reasoning','')[:100]}"
        for r in bad_calls)

    prompt = (
        f"You are a prompt engineer. Here are failures in bad calls:\n{issues}\n\n"
        f"Current system prompt (first 1500 chars):\n{prompt_text[:1500]}\n\n"
        f"Suggest 3 specific, concrete changes to the system prompt to fix these issues. "
        f"Quote the exact part to change and what to replace it with. Be brief."
    )
    print("\nGenerating improvement suggestions...")
    result = call_llm(
        [{"role": "system", "content": "You are a prompt engineer. Be specific and brief."},
         {"role": "user",   "content": prompt}],
        max_tokens=500)
    if result:
        print(f"\n{'='*65}\nSUGGESTED IMPROVEMENTS:\n{'='*65}")
        print(result)
        with open(os.path.join(output_dir, "suggestions.txt"), "w", encoding="utf-8") as f:
            f.write(result)


# ── Main ──────────────────────────────────────────────────────────────────────

def run_pipeline(prompt_path, transcripts_folder, output_dir, suggest=False):
    os.makedirs(output_dir, exist_ok=True)

    prompt_text = load_text(prompt_path)
    prompt_name = os.path.basename(prompt_path)
    files = sorted([f for f in glob.glob(os.path.join(transcripts_folder, "*.json"))
                    if not os.path.basename(f).startswith("_")])

    print(f"\nPrompt      : {prompt_name}")
    print(f"Transcripts : {len(files)} files")
    print(f"Keys        : {len(KEY_POOL)} loaded")
    print(f"Output      : {output_dir}\n")

    all_results = []

    for i, filepath in enumerate(files):
        data = load_json(filepath)
        if isinstance(data, list): continue

        call_id     = data.get("call_id", "unknown")
        customer    = data.get("customer", {})
        disposition = data.get("disposition", "unknown")

        print(f"[{i+1}/{len(files)}] {call_id} | {customer.get('name')} | {disposition}")

        filled  = fill_variables(prompt_text, customer)[:3000]
        msgs    = get_customer_msgs(data)

        # Step 1: Simulate — run borrower messages through the LLM
        # using the provided prompt as the agent system prompt
        # This is the core of the pipeline — testing the prompt
        print(f"  Simulating with prompt...")
        simulated = simulate_call(filled, msgs, max_turns=6)
        time.sleep(1)

        # Step 2: Score the simulated conversation
        # Same criteria as Part 1 evaluator
        print(f"  Scoring...")
        score_result = score_conversation(
            simulated, call_id, customer.get("name","?"), disposition)
        time.sleep(1)

        print(f"  Score: {score_result.get('score')}/100 → {score_result.get('verdict','?').upper()}")

        result = {
            "call_id": call_id, "customer_name": customer.get("name","?"),
            "disposition": disposition, "simulated": simulated,
            "score_result": score_result,
        }
        all_results.append(result)
        save_json(result, os.path.join(output_dir, f"{call_id}_result.json"))

        if i < len(files) - 1:
            time.sleep(2)

    print(f"\n{'='*65}\nGENERATING REPORT\n{'='*65}\n")
    generate_report(prompt_name, all_results, output_dir)

    if suggest:
        suggest_improvements(all_results, prompt_text, output_dir)

    print(f"\nDone. Results saved to: {output_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Score any system prompt against all transcripts in one command")
    parser.add_argument("--prompt",      required=True,  help="Path to system prompt file")
    parser.add_argument("--transcripts", required=True,  help="Path to transcripts folder")
    parser.add_argument("--output",      default=None,   help="Output folder (auto-named if not set)")
    parser.add_argument("--suggest",     action="store_true",
                        help="Bonus: auto-suggest prompt improvements")
    args = parser.parse_args()

    if args.output is None:
        name      = os.path.splitext(os.path.basename(args.prompt))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir   = os.path.join("results", f"pipeline_{name}_{timestamp}")
    else:
        out_dir = args.output

    run_pipeline(args.prompt, args.transcripts, out_dir, args.suggest)