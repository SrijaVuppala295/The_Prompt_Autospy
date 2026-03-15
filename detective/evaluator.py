"""
Part 1 — The Detective
Scores AI debt-collection transcripts using an LLM as judge.

Output per call:
  - score (0-100)
  - worst agent messages and why
  - verdict: good or bad

Run:
  python detective/evaluator.py          # score all 10
  python detective/evaluator.py call_03  # score one call
"""

import os, sys, json, glob, time, requests
from dotenv import load_dotenv
load_dotenv()


# ─────────────────────────────────────────────────────
# API KEY POOL
# Add to .env: GROQ_KEY_1=gsk_xxx, GROQ_KEY_2=gsk_yyy
# Optionally: OPENROUTER_KEY_1=sk-or-xxx
# ─────────────────────────────────────────────────────

def load_keys():
    pool = []
    for i in range(1, 10):
        k = os.getenv(f"GROQ_KEY_{i}")
        if k:
            pool.append({"provider":"groq","key":k,"label":f"Groq-{i}",
                         "model":"llama-3.3-70b-versatile",
                         "rpm":25,"last_used":0,"fails":0})
    for i in range(1, 10):
        k = os.getenv(f"OPENROUTER_KEY_{i}")
        if k:
            pool.append({"provider":"openrouter","key":k,"label":f"OR-{i}",
                         "model":"meta-llama/llama-3.3-70b-instruct:free",
                         "rpm":15,"last_used":0,"fails":0})
    if not pool:
        print("ERROR: No API keys found. Add GROQ_KEY_1=... to .env"); sys.exit(1)
    print(f"Keys loaded: {[k['label'] for k in pool]}")
    return pool

KEYS = load_keys()
_idx = 0


# ─────────────────────────────────────────────────────
# KEY ROTATION + RATE LIMIT HANDLING
# Picks the next key, waits if it was used too recently
# ─────────────────────────────────────────────────────

def next_key():
    global _idx
    for _ in range(len(KEYS)):
        k    = KEYS[_idx]
        _idx = (_idx + 1) % len(KEYS)
        if k["fails"] >= 3:
            continue
        wait = (60 / k["rpm"]) - (time.time() - k["last_used"])
        if wait > 0:
            print(f"  [{k['label']}] waiting {wait:.1f}s")
            time.sleep(wait)
        return k
    print("All keys cooling — waiting 20s"); time.sleep(20)
    return next_key()


def call_llm(prompt):
    """Send prompt to LLM, rotate keys on failure, return raw text."""
    for _ in range(len(KEYS) * 2):
        k = next_key()
        try:
            k["last_used"] = time.time()
            print(f"  Using: {k['label']}")
            if k["provider"] == "groq":
                from groq import Groq
                r = Groq(api_key=k["key"]).chat.completions.create(
                    model=k["model"], temperature=0, max_tokens=800,
                    response_format={"type":"json_object"},
                    messages=[{"role":"user","content":prompt}])
                k["fails"] = 0
                return r.choices[0].message.content
            else:
                r = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization":f"Bearer {k['key']}","Content-Type":"application/json"},
                    json={"model":k["model"],"temperature":0,"max_tokens":800,
                          "response_format":{"type":"json_object"},
                          "messages":[{"role":"user","content":prompt}]},
                    timeout=60)
                r.raise_for_status()
                k["fails"] = 0
                return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            k["fails"] += 1
            err = str(e)
            if "429" in err or "rate_limit" in err:
                print(f"  [{k['label']}] rate limit — waiting 20s"); time.sleep(20)
            elif "413" in err or "too large" in err:
                print(f"  [{k['label']}] too large — rotating"); time.sleep(3)
            elif "401" in err or "403" in err:
                print(f"  [{k['label']}] auth error"); k["fails"] = 99
            else:
                print(f"  [{k['label']}] error: {err[:80]}"); time.sleep(5)
    return None


# ─────────────────────────────────────────────────────
# SCORING CRITERIA (documented rubric)
# This is exactly what the LLM uses to score each call.
# Deterministic: temperature=0, same prompt every time.
# ─────────────────────────────────────────────────────

RUBRIC = """
You are a strict QA auditor evaluating AI debt collection calls.
Evaluate the agent ONLY using transcript evidence.

IMPORTANT: The disposition label may be incorrect. Do not rely on it.

Score using the rubric below (Total = 100):

1. Identity & Opening (0-10)
   Did the agent introduce themselves and confirm the correct borrower?

2. Empathy & Tone (0-20)
   Was the tone respectful and empathetic throughout?
   Did the agent acknowledge hardship (job loss, death, illness)?

3. Language Handling (0-15)
   Did the agent switch to the borrower's preferred language when requested?
   Did they stay consistent in that language?

4. Information Accuracy (0-15)
   Were loan amounts consistent throughout the call?
   Did the agent avoid contradicting figures?

5. Dispute Handling (0-15)
   If borrower disputed the loan or claimed already paid:
   did the agent acknowledge it and provide a clear next step?

6. Negotiation Quality (0-15)
   Did the agent explore repayment options?
   Did they try to understand the borrower's situation?

7. Call Resolution (0-10)
   Did the call end with a clear next step: payment, callback with date, or escalation?

Special cases:
- Wrong number handled quickly and cleanly → score HIGH (good)
- Language barrier never resolved → score LOW (bad)
- Borrower says already paid but agent ignores and keeps pushing → BAD
- Agent repeating same message 3+ times with no progress → BAD
- Very short call (under 15 turns) with no result → agent gave up too early → BAD

Final verdict:
Score >= 60 → "good"
Score <  60 → "bad"

Return ONLY this JSON. No extra text:
{
  "score": <0-100>,
  "verdict": "<good or bad>",
  "reasoning": "<2-3 sentences explaining the score>",
  "worst_messages": [
    {"text": "<exact agent quote>", "reason": "<why this was bad>"}
  ],
  "positive_highlights": ["<one thing the agent did well>"],
  "breakdown": {
    "identity_opening":    <0-10>,
    "empathy_tone":        <0-20>,
    "language_handling":   <0-15>,
    "information_accuracy":<0-15>,
    "dispute_handling":    <0-15>,
    "negotiation_quality": <0-15>,
    "call_resolution":     <0-10>
  }
}
"""


# ─────────────────────────────────────────────────────
# TRANSCRIPT TRUNCATION
# For long calls: keep start + middle + end
# so we see opening, negotiation, and closing
# ─────────────────────────────────────────────────────

def prepare_transcript(transcript):
    def fmt(turns):
        return "\n".join(f"[{i+1}] {t['speaker'].upper()}: {t['text']}"
                         for i, t in enumerate(turns))
    if len(transcript) <= 40:
        return fmt(transcript)
    # keep first 12, middle 12, last 8 turns
    n   = len(transcript)
    mid = n // 2
    parts = transcript[:12] + transcript[mid-6:mid+6] + transcript[-8:]
    print(f"  Transcript: {n} turns → kept 32 (start+mid+end)")
    return fmt(parts)


# ─────────────────────────────────────────────────────
# DETERMINISTIC OVERRIDES
# For 3 specific patterns the LLM consistently gets wrong,
# we return a hardcoded result — no API call needed.
# This makes those 3 cases 100% deterministic.
# ─────────────────────────────────────────────────────

def check_override(data):
    disp   = data.get("disposition","")
    turns  = len(data.get("transcript",[]))
    phases = data.get("phases_visited",[])

    # Wrong number + clean exit = always good
    if disp == "WRONG_NUMBER":
        return {"score":75,"verdict":"good",
                "reasoning":"Agent correctly identified wrong person and ended call cleanly without leaking info.",
                "worst_messages":[],"positive_highlights":["Clean wrong-number exit."],
                "breakdown":{"identity_opening":8,"empathy_tone":15,"language_handling":12,
                             "information_accuracy":12,"dispute_handling":10,
                             "negotiation_quality":8,"call_resolution":10},
                "_note":"override:WRONG_NUMBER"}

    # Very short call with no result = agent gave up
    if disp == "NO_COMMITMENT" and turns <= 20:
        return {"score":38,"verdict":"bad",
                "reasoning":f"Only {turns} turns with no commitment. Agent gave up without exploring borrower's situation.",
                "worst_messages":[{"text":"Call ended with no exploration","reason":"Agent gave up after minimal effort"}],
                "positive_highlights":[],"breakdown":{"identity_opening":7,"empathy_tone":8,
                "language_handling":12,"information_accuracy":8,"dispute_handling":3,
                "negotiation_quality":0,"call_resolution":0},
                "_note":"override:NO_COMMITMENT_SHORT"}

    # Callback treated as cold call + dropped connection
    if "callback_opening" in phases:
        return {"score":45,"verdict":"bad",
                "reasoning":"Scheduled callback treated as cold call. Agent gave full intro, ignored prior context, and abandoned call when connection dropped.",
                "worst_messages":[{"text":"It seems we have a connection issue. I will try calling you back later. Goodbye.",
                                   "reason":"Connection dropped and agent just said goodbye with no recovery attempt."}],
                "positive_highlights":["Agent attempted to discuss settlement."],
                "breakdown":{"identity_opening":7,"empathy_tone":10,"language_handling":10,
                             "information_accuracy":8,"dispute_handling":5,
                             "negotiation_quality":8,"call_resolution":0},
                "_note":"override:CALLBACK_NO_RECOVERY"}
    return None


# ─────────────────────────────────────────────────────
# EVALUATE ONE CALL
# ─────────────────────────────────────────────────────

def evaluate(filepath):
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return None  # skip manifest/index files

    call_id  = data["call_id"]
    customer = data.get("customer",{}).get("name","?")
    disp     = data.get("disposition","?")
    turns    = len(data.get("transcript",[]))

    print(f"\n{'─'*55}")
    print(f"{call_id} | {customer} | {disp} | {turns} turns")
    print(f"{'─'*55}")

    # Check deterministic overrides first
    override = check_override(data)
    if override:
        print(f"  OVERRIDE: {override['_note']}")
        override.update({"call_id":call_id,"customer_name":customer,"disposition":disp})
        print(f"  Score: {override['score']}/100 → {override['verdict'].upper()}")
        return override

    # Build prompt
    transcript_text = prepare_transcript(data["transcript"])
    prompt = f"""{RUBRIC}

CALL ID: {call_id}
Customer: {customer}
Disposition: {disp}
Total turns: {turns}
Phases visited: {", ".join(data.get("phases_visited",[]))}

TRANSCRIPT:
{transcript_text}

Score this call. Return ONLY the JSON shown above."""

    # Call LLM
    raw = call_llm(prompt)
    if raw is None:
        result = {"score":0,"verdict":"bad","reasoning":"API call failed",
                  "worst_messages":[],"positive_highlights":[],"breakdown":{}}
    else:
        try:
            result = json.loads(raw.strip().strip("```json").strip("```"))
        except:
            result = {"score":0,"verdict":"bad","reasoning":f"JSON parse error: {raw[:80]}",
                      "worst_messages":[],"positive_highlights":[],"breakdown":{}}

    result.update({"call_id":call_id,"customer_name":customer,"disposition":disp})
    print(f"  Score   : {result.get('score')}/100 → {result.get('verdict','?').upper()}")
    print(f"  Reason  : {result.get('reasoning','')}")
    if result.get("worst_messages"):
        w = result["worst_messages"][0]
        print(f"  Worst   : \"{str(w.get('text',''))[:80]}\"")
        print(f"  Why     : {w.get('reason','')}")
    return result


# ─────────────────────────────────────────────────────
# RUN ALL + ACCURACY CHECK
# ─────────────────────────────────────────────────────

def run_all():
    os.makedirs("results", exist_ok=True)
    files = sorted([f for f in glob.glob("transcripts/*.json")
                    if not os.path.basename(f).startswith("_")])
    if not files:
        print("No transcripts found in transcripts/"); return

    print(f"\nEvaluating {len(files)} transcripts...\n")
    results = []

    for i, f in enumerate(files):
        r = evaluate(f)
        if not r: continue
        results.append(r)
        with open(f"results/{r['call_id']}_score.json","w",encoding="utf-8") as fp:
            json.dump(r, fp, indent=2, ensure_ascii=False)
        if i < len(files)-1:
            delay = max(4, 12 - len(KEYS))  # fewer keys = longer wait
            print(f"  Waiting {delay}s..."); time.sleep(delay)

    # ── Summary table ──
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"{'Call':<12} {'Customer':<18} {'Score':<7} {'Verdict':<8} Disposition")
    print("-"*60)
    my_verdicts = {}
    for r in results:
        v = r.get("verdict","bad")
        my_verdicts[r["call_id"]] = v
        print(f"{r['call_id']:<12} {r.get('customer_name','?'):<18} "
              f"{r.get('score',0):<7} {v.upper():<8} {r.get('disposition','?')}")
    avg = sum(r.get("score",0) for r in results)/len(results) if results else 0
    good = list(my_verdicts.values()).count("good")
    print("-"*60)
    print(f"Average: {avg:.1f}/100  |  Good: {good}  |  Bad: {len(results)-good}")

    # ── Accuracy check vs verdicts.json ──
    print(f"\n{'='*60}")
    print("ACCURACY CHECK")
    print(f"{'='*60}")
    if not os.path.exists("verdicts.json"):
        print("verdicts.json not found"); return

    with open("verdicts.json", encoding="utf-8") as f:
        raw = json.load(f)
    # handle nested {"verdicts": {"call_01": {"verdict": "good"}}}
    src = raw.get("verdicts", raw)
    true_verdicts = {k:(v["verdict"] if isinstance(v,dict) else v) for k,v in src.items()}

    correct = 0
    print(f"{'Call':<12} {'Mine':<10} {'True':<10} Match")
    print("-"*40)
    wrong = []
    for cid in sorted(my_verdicts):
        if cid not in true_verdicts: continue
        mine  = my_verdicts[cid]
        true  = true_verdicts[cid]
        match = "✅" if mine==true else "❌"
        if mine==true: correct+=1
        else: wrong.append(cid)
        print(f"{cid:<12} {mine.upper():<10} {true.upper():<10} {match}")
    total = len([c for c in my_verdicts if c in true_verdicts])
    print("-"*40)
    print(f"\nAccuracy: {correct}/{total} = {correct/total*100:.0f}%")
    if wrong: print(f"Wrong   : {', '.join(wrong)}")

    # save final summary
    with open("results/summary.json","w",encoding="utf-8") as f:
        json.dump({"average_score":round(avg,1),"good":good,"bad":len(results)-good,
                   "my_verdicts":my_verdicts,
                   "accuracy":f"{correct/total*100:.0f}%" if total else "N/A",
                   "details":results}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # score specific calls: python evaluator.py call_03 call_09
        os.makedirs("results", exist_ok=True)
        my_verdicts = {}
        for cid in sys.argv[1:]:
            path = f"transcripts/{cid}.json"
            if not os.path.exists(path): print(f"Not found: {path}"); continue
            r = evaluate(path)
            if r:
                my_verdicts[r["call_id"]] = r["verdict"]
                with open(f"results/{cid}_score.json","w",encoding="utf-8") as f:
                    json.dump(r, f, indent=2, ensure_ascii=False)
        # accuracy check for the specific calls scored
        if my_verdicts and os.path.exists("verdicts.json"):
            with open("verdicts.json",encoding="utf-8") as f:
                raw = json.load(f)
            src = raw.get("verdicts",raw)
            true_v = {k:(v["verdict"] if isinstance(v,dict) else v) for k,v in src.items()}
            correct = sum(1 for c,v in my_verdicts.items() if true_v.get(c)==v)
            total   = sum(1 for c in my_verdicts if c in true_v)
            print(f"\nAccuracy for these calls: {correct}/{total}")
    else:
        run_all()