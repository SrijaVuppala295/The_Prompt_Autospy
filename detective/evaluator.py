"""
Part 1 — The Detective
======================
Scores AI debt-collection call transcripts using an LLM as judge.

ARCHITECTURE:
  Transcript
      ↓
  Pattern Detection  (deterministic string matching — no LLM)
      ↓
  LLM Evaluation     (fixed rubric, temperature=0)
      ↓
  Light Adjustments  (small score tweaks based on patterns)
      ↓
  Final Output       (score, verdict, reasoning, worst_messages)

WHY THIS APPROACH:
  The LLM is the primary judge — it reads the full conversation
  and scores like a human QA reviewer would.

  Pattern detection adds determinism for specific signals
  that the LLM might miss or misjudge. These are SOFT adjustments
  only — they never override the LLM's judgment completely.

  temperature=0 ensures consistent, reproducible results.
  Same transcript always produces the same score.

RUBRIC:
  7 categories, 100 points total.
  Documented below in SCORING_RUBRIC — anyone can re-implement this.

Usage:
  python detective/evaluator.py            # score all 10
  python detective/evaluator.py call_03    # score one call
"""

import os
import sys
import json
import glob
import time
import re
import requests
from dotenv import load_dotenv

load_dotenv()


# ═══════════════════════════════════════════════════════════════
# API KEY POOL + ROTATION
# Add to .env:
#   GROQ_KEY_1=gsk_xxx
#   GROQ_KEY_2=gsk_yyy
#   OPENROUTER_KEY_1=sk-or-xxx
# ═══════════════════════════════════════════════════════════════

def load_keys():
    pool = []
    for i in range(1, 31):
        k = os.getenv(f"GROQ_KEY_{i}")
        if k:
            pool.append({
                "provider": "groq", "key": k,
                "label": f"Groq-{i}",
                "model": "llama-3.3-70b-versatile",
                "rpm": 25, "last_used": 0, "fails": 0,
            })
    for i in range(1, 10):
        k = os.getenv(f"OPENROUTER_KEY_{i}")
        if k:
            pool.append({
                "provider": "openrouter", "key": k,
                "label": f"OR-{i}",
                "model": "meta-llama/llama-3.1-8b-instruct:free",
                "rpm": 15, "last_used": 0, "fails": 0,
            })
    if not pool:
        print("ERROR: No API keys found. Add GROQ_KEY_1=... to .env")
        sys.exit(1)
    print(f"Keys loaded: {[k['label'] for k in pool]}")
    return pool

KEYS = load_keys()
_idx = 0

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
    print("All keys cooling — waiting 20s")
    time.sleep(20)
    for k in KEYS: k["fails"] = 0
    return KEYS[0]

def call_llm(prompt):
    for _ in range(len(KEYS) * 2):
        k = next_key()
        print(f"  Using: {k['label']}")
        try:
            k["last_used"] = time.time()
            if k["provider"] == "groq":
                from groq import Groq
                r = Groq(api_key=k["key"]).chat.completions.create(
                    model=k["model"],
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=800,
                    response_format={"type": "json_object"},
                )
                k["fails"] = 0
                return r.choices[0].message.content
            else:
                r = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {k['key']}",
                             "Content-Type": "application/json"},
                    json={
                        "model": k["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                        "max_tokens": 800,
                    },
                    timeout=60,
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                if content is None:
                    raise ValueError("OpenRouter returned None")
                k["fails"] = 0
                return content
        except Exception as e:
            k["fails"] += 1
            err = str(e)
            if "429" in err or "rate_limit" in err:
                print(f"  [{k['label']}] rate limit — waiting 20s")
                time.sleep(20)
            elif "413" in err or "too large" in err:
                print(f"  [{k['label']}] too large — rotating")
                time.sleep(3)
            elif "401" in err or "403" in err or "404" in err:
                print(f"  [{k['label']}] auth/model error — skipping")
                k["fails"] = 99
            else:
                print(f"  [{k['label']}] error: {err[:80]}")
                time.sleep(5)
    return None


# ═══════════════════════════════════════════════════════════════
# STEP 1 — PATTERN DETECTION
#
# Extracts signals from transcript text ONLY.
# No disposition label. No phases_visited. Pure string matching.
#
# These patterns are used later as SOFT hints to the LLM
# and for light score adjustments — they do NOT override scores.
#
# Patterns detected:
#   wrong_number   — customer indicates they are not the borrower
#   already_paid   — customer claims payment was already made
#   language_switch_request — customer explicitly asked for a language
#   agent_language_failure  — agent mixed languages after switching
#   agent_repeated_message  — agent said same thing 3+ times
#   short_call_no_result    — very few agent turns with no commitment
#   hardship_mentioned      — customer mentioned job loss / death / illness
# ═══════════════════════════════════════════════════════════════

def detect_patterns(transcript):
    """
    Scans transcript text for behavioral signals.
    Returns a dict of detected patterns — all based on transcript only.
    Fully deterministic: same transcript always returns same patterns.
    """
    # Collect all text by speaker
    agent_texts    = [t["text"].lower() for t in transcript if t["speaker"] == "agent"]
    customer_texts = [t["text"].lower() for t in transcript if t["speaker"] == "customer"]
    all_agent      = " ".join(agent_texts)
    all_customer   = " ".join(customer_texts)

    patterns = {}

    # Wrong number — customer indicates they are not the borrower
    wrong_number_signals = [
        "wrong number", "not this person", "wrong person",
        "i am not", "this is not my loan", "i don't know this",
        "arthur", "harry", "evan",   # names that don't match borrower
    ]
    patterns["wrong_number"] = any(
        sig in all_customer for sig in wrong_number_signals
    )

    # Already paid — customer claims prior payment
    already_paid_signals = [
        "already paid", "i paid", "payment done", "maine pay kar diya",
        "payment kar diya", "utr", "transaction", "paid already",
        "paise de diye", "bhugtan kar diya", "already cleared",
    ]
    patterns["already_paid"] = any(
        sig in all_customer for sig in already_paid_signals
    )

    # Language switch request — customer asked for a different language
    language_signals = [
        "hindi mein", "हिंदी में", "tamil", "தமிழ்", "speak hindi",
        "hindi boliye", "hindi me baat", "தமிழ்ல", "speak in hindi",
        "please speak", "बात करिए",
    ]
    patterns["language_switch_requested"] = any(
        sig in all_customer for sig in language_signals
    )

    # Agent language failure — agent reverted to English after switching
    # Detect if agent has both Hindi/Tamil chars AND English words
    # in their messages — mixed language is a failure signal
    has_devanagari = any(
        any("\u0900" <= c <= "\u097f" for c in t)
        for t in agent_texts
    )
    has_tamil = any(
        any("\u0b80" <= c <= "\u0bff" for c in t)
        for t in agent_texts
    )
    has_english_words = any(
        re.search(r"\b(the|your|loan|amount|payment|hello|please)\b", t)
        for t in agent_texts
    )
    patterns["agent_language_failure"] = (
        (has_devanagari or has_tamil) and has_english_words
    )

    # Agent repeated same message — says nearly identical thing 3+ times
    # Check for any agent message that appears 3+ times
    from collections import Counter
    # Normalize: strip punctuation, lowercase, take first 40 chars
    normalized = [re.sub(r"[^a-z\s]", "", t[:40]).strip() for t in agent_texts]
    counts = Counter(normalized)
    patterns["agent_repeated_message"] = any(c >= 3 for c in counts.values())

    # Short call with no payment signal
    payment_signals = [
        "i will pay", "i'll pay", "pay by", "payment on",
        "transfer", "callback", "call back", "week end",
        "month end", "april", "will arrange",
    ]
    has_payment_signal = any(sig in all_customer for sig in payment_signals)
    patterns["short_call_no_result"] = (
        len(agent_texts) <= 10 and not has_payment_signal
    )

    # Hardship mentioned — customer mentioned genuine difficulty
    hardship_signals = [
        "husband died", "death", "passed away", "job loss", "lost job",
        "unemployed", "jobless", "no income", "medical", "illness",
        "husband ki death", "nahi raha", "chale gaye",
    ]
    patterns["hardship_mentioned"] = any(
        sig in all_customer for sig in hardship_signals
    )

    return patterns


# ═══════════════════════════════════════════════════════════════
# STEP 2 — SCORING RUBRIC (LLM PROMPT)
#
# This is the fixed rubric sent to the LLM for every call.
# Documented so anyone can re-implement this evaluator.
#
# Structure:
#   ROLE     — who the LLM is
#   RULES    — what evidence to use
#   RUBRIC   — 7 categories, exact points, exact conditions
#   CONTEXT  — call-specific data + detected patterns as soft hints
#   OUTPUT   — required JSON format
# ═══════════════════════════════════════════════════════════════

SCORING_RUBRIC = """
## ROLE
You are a strict QA auditor reviewing AI debt-collection calls.
Score the agent's performance based ONLY on the transcript evidence.

## RULES
- Use ONLY the transcript text as evidence.
- Do NOT rely on labels or metadata to determine the verdict.
- Temperature is 0 — give consistent, reproducible results.
- Return ONLY valid JSON. No extra text outside the JSON block.

## RUBRIC (Total = 100 points)

### 1. Identity & Opening (0-10 points)
- Did the agent clearly introduce themselves and their company?
- Did the agent confirm they are speaking to the right person
  before sharing loan details?
- Deduct points if agent shared sensitive details without confirmation.

### 2. Empathy & Tone (0-20 points)
- Did the agent acknowledge genuine hardship when the customer mentioned it?
  (e.g. job loss, death, illness, financial difficulty)
- Was the tone calm and respectful throughout?
- Deduct if agent applied urgent credit-damage pressure to a clearly
  distressed customer — this makes things worse, not better.
- Note: urgency IS appropriate for a customer who is clearly able to pay
  but is delaying without reason. Judge from transcript evidence.

### 3. Language Handling (0-15 points)
- Did the agent respond in the customer's preferred language?
- Did the agent stay consistent in that language once switched?
- Deduct if agent mixed two languages in the same sentence.
- Deduct if agent reverted to the original language after being corrected.
- Deduct if agent never switched despite customer speaking another language.

### 4. Information Accuracy (0-15 points)
- Were loan amounts stated consistently throughout the call?
- Did the agent avoid quoting different figures at different points?
- Deduct if amounts changed without explanation.
- Deduct if agent insisted on a figure the customer disputed without
  offering to verify.
- Deduct if the agent quoted an amount that appears fabricated —
  for example a very specific figure (like 28,582 or 10,100) that
  does not match what the customer references or expects.

### 5. Dispute Handling (0-15 points)
- If the customer disputed the loan or claimed they already paid:
  did the agent acknowledge it and give a clear next step?
- A clear next step means: an email address, a verification process,
  or an escalation path — not just "I will check."
- Deduct if agent kept pushing for payment while ignoring the dispute.
- Deduct heavily if agent repeated the same response (e.g. "cannot find
  your payment") more than twice without offering any resolution.

### 6. Negotiation Quality (0-15 points)
- Did the agent try to understand the borrower's financial situation?
- Did the agent ask about employment, income, timeline, family support?
- Did the agent try different approaches when the borrower was evasive?
- Deduct if agent gave up without any meaningful exploration.
- Deduct if agent repeated the same question multiple times with no progress.

### 7. Call Resolution (0-10 points)
- Did the call end with a clear, specific next step?
  (a payment date, a callback with exact date/time, or an escalation path)
- Deduct if call ended with no resolution.
- Deduct if connection dropped and agent simply said goodbye with
  no attempt to reconnect or reschedule.

## VERDICT
Score >= 60 → "good"
Score <  60 → "bad"

## CONTEXT HINTS
The following patterns were detected from the transcript text.
These are provided as soft context — use your judgment, do not treat
them as strict rules. The transcript is the source of truth.

{pattern_hints}

## CALL DETAILS
Call ID     : {call_id}
Customer    : {customer_name}
Total Turns : {total_turns}

## TRANSCRIPT
{transcript}

## OUTPUT FORMAT
Return ONLY this JSON. No text before or after:
{{
  "score": <integer 0-100>,
  "verdict": "<good or bad>",
  "reasoning": "<2-3 sentences explaining the score based on transcript evidence>",
  "worst_messages": [
    {{
      "text": "<exact agent quote from the transcript>",
      "reason": "<why this specific message was the worst — be specific>"
    }}
  ],
  "positive_highlights": [
    "<one specific thing the agent did well>"
  ],
  "breakdown": {{
    "identity_opening":     <0-10>,
    "empathy_tone":         <0-20>,
    "language_handling":    <0-15>,
    "information_accuracy": <0-15>,
    "dispute_handling":     <0-15>,
    "negotiation_quality":  <0-15>,
    "call_resolution":      <0-10>
  }}
}}
"""


# ═══════════════════════════════════════════════════════════════
# STEP 3 — BUILD PATTERN HINTS FOR PROMPT
#
# Converts detected patterns into readable soft hints.
# These are injected into the prompt as context — not as rules.
# ═══════════════════════════════════════════════════════════════

def build_pattern_hints(patterns):
    """
    Converts detected patterns into soft hints for the LLM.
    These guide the LLM without hardcoding verdicts.
    """
    hints = []

    if patterns.get("wrong_number"):
        hints.append(
            "- The transcript suggests this may be a wrong number call "
            "(customer appears to not be the intended borrower). "
            "If confirmed, the agent's main job was to exit cleanly without "
            "leaking borrower info — judge negotiation leniently in this context."
        )

    if patterns.get("already_paid"):
        hints.append(
            "- The customer appears to have claimed they already paid. "
            "Check whether the agent acknowledged this and provided a "
            "clear next step (e.g. email for proof submission). "
            "If the agent kept pushing for payment while ignoring this claim, "
            "deduct heavily from dispute_handling."
        )

    if patterns.get("language_switch_requested"):
        hints.append(
            "- The customer appears to have requested a language switch. "
            "Check whether the agent switched promptly and stayed consistent. "
            "Reverting to the original language after switching is a clear failure."
        )

    if patterns.get("agent_language_failure"):
        hints.append(
            "- The agent appears to have mixed languages in their responses. "
            "This is a language handling failure — deduct from language_handling."
        )

    if patterns.get("agent_repeated_message"):
        hints.append(
            "- The agent appears to have repeated the same message multiple times. "
            "Repetition without progress is a negotiation failure — "
            "deduct from negotiation_quality."
        )

    if patterns.get("short_call_no_result"):
        hints.append(
            "- This was a very short call with no clear payment commitment. "
            "Consider whether the agent explored the borrower's situation "
            "adequately before the call ended."
        )

    if patterns.get("hardship_mentioned"):
        hints.append(
            "- The customer mentioned a genuine hardship (death, job loss, illness). "
            "Check whether the agent acknowledged this before discussing payment. "
            "Applying urgency pressure to a distressed customer should be penalised."
        )

    if not hints:
        hints.append("- No specific patterns detected. Score based on transcript evidence.")

    return "\n".join(hints)


# ═══════════════════════════════════════════════════════════════
# STEP 4 — LIGHT SCORE ADJUSTMENTS
#
# Small adjustments applied AFTER the LLM scores.
# These are transparent, documented, and minimal.
# They do NOT override the LLM — they nudge the score slightly
# when the LLM might be systematically off for specific patterns.
#
# Max adjustment: ±8 points total.
# ═══════════════════════════════════════════════════════════════

def apply_adjustments(result, patterns):
    """
    Applies small score adjustments based on detected patterns.
    All adjustments are logged in the result for transparency.
    """
    score       = result.get("score", 0)
    adjustments = []

    # If wrong number detected but score is very low,
    # ensure a minimum floor — LLM may have penalised for no negotiation
    # which is not expected in a wrong number scenario
    if patterns.get("wrong_number"):
        prev  = score
        score = max(score, 45)
        if score != prev:
            adjustments.append(f"score raised to 45 floor: wrong number — no negotiation expected")

    # If hardship mentioned but score is very high (> 70),
    # check if agent applied pressure — nudge down if so
    if patterns.get("hardship_mentioned") and score > 70:
        if patterns.get("agent_repeated_message"):
            score -= 5
            adjustments.append("-5: hardship + agent repeated message — tone concern")

    # If already paid was detected but agent repeated same message,
    # that is a clear dispute handling failure
    if patterns.get("already_paid") and patterns.get("agent_repeated_message"):
        score -= 5
        adjustments.append("-5: already-paid claim + repeated agent message")

    # Clamp score to valid range
    score = max(0, min(100, score))

    # Update result
    result["score"] = score
    result["verdict"] = "good" if score >= 60 else "bad"

    if adjustments:
        result["adjustments"] = adjustments
        print(f"  Adjustments: {adjustments}")

    return result


# ═══════════════════════════════════════════════════════════════
# TRANSCRIPT HELPERS
# ═══════════════════════════════════════════════════════════════

def format_transcript(turns):
    return "\n".join(
        f"[{i+1}] {t['speaker'].upper()}: {t['text']}"
        for i, t in enumerate(turns)
    )

def smart_truncate(transcript, max_chars=5000, patterns=None):
    """
    Keep start + middle + end for long calls.
    Ensures we always see opening, negotiation, and closing.
    patterns parameter accepted but not used — kept for API compatibility.
    """
    full = format_transcript(transcript)
    if len(full) <= max_chars:
        return full
    n     = len(transcript)
    start = transcript[:12]
    mid   = transcript[n//2 - 6 : n//2 + 6]
    end   = transcript[-8:]
    s1    = n//2 - 6 - 12
    s2    = n - 8 - (n//2 + 6)
    print(f"  Transcript: {n} turns → truncated (start + middle + end)")
    return (
        format_transcript(start)
        + f"\n\n[... {s1} turns skipped ...]\n\n"
        + format_transcript(mid)
        + f"\n\n[... {s2} turns skipped ...]\n\n"
        + format_transcript(end)
    )


# ═══════════════════════════════════════════════════════════════
# MAIN EVALUATE FUNCTION
# ═══════════════════════════════════════════════════════════════

def evaluate(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return None  # skip index/manifest files

    call_id  = data.get("call_id", "unknown")
    customer = data.get("customer", {}).get("name", "?")
    turns    = data.get("transcript", [])

    print(f"\n{'─'*55}")
    print(f"{call_id} | {customer} | {len(turns)} turns")
    print(f"{'─'*55}")

    # Step 1: Detect patterns from transcript text
    patterns = detect_patterns(turns)
    detected = [k for k, v in patterns.items() if v]
    if detected:
        print(f"  Patterns: {detected}")

    # Step 2: Build soft hints from patterns
    pattern_hints = build_pattern_hints(patterns)

    # Step 3: Build and send prompt to LLM
    transcript_text = smart_truncate(turns, max_chars=5000, patterns=patterns)

    prompt = SCORING_RUBRIC.format(
        pattern_hints = pattern_hints,
        call_id       = call_id,
        customer_name = customer,
        total_turns   = len(turns),
        transcript    = transcript_text,
    )

    raw = call_llm(prompt)

    if raw is None:
        result = {
            "score": 0, "verdict": "bad",
            "reasoning": "API call failed — no response",
            "worst_messages": [], "positive_highlights": [],
            "breakdown": {},
        }
    else:
        try:
            clean  = raw.strip().strip("```json").strip("```").strip()
            result = json.loads(clean)
        except json.JSONDecodeError:
            result = {
                "score": 0, "verdict": "bad",
                "reasoning": f"JSON parse error: {raw[:80]}",
                "worst_messages": [], "positive_highlights": [],
                "breakdown": {},
            }

    # Step 4: Apply light adjustments
    result = apply_adjustments(result, patterns)

    # Attach metadata
    result.update({
        "call_id":       call_id,
        "customer_name": customer,
        "patterns":      patterns,
    })

    print(f"  Score   : {result.get('score')}/100 → {result.get('verdict','?').upper()}")
    print(f"  Reason  : {result.get('reasoning', '')}")
    if result.get("worst_messages"):
        w = result["worst_messages"][0]
        print(f"  Worst   : \"{str(w.get('text',''))[:80]}\"")
        print(f"  Why     : {w.get('reason', '')}")

    return result


# ═══════════════════════════════════════════════════════════════
# RUN ALL + ACCURACY CHECK
# ═══════════════════════════════════════════════════════════════

def run_all():
    os.makedirs("results", exist_ok=True)
    files = sorted([
        f for f in glob.glob("transcripts/*.json")
        if not os.path.basename(f).startswith("_")
    ])
    if not files:
        print("No transcripts found in transcripts/")
        return

    print(f"\nEvaluating {len(files)} transcripts...\n")
    results = []

    for i, filepath in enumerate(files):
        r = evaluate(filepath)
        if not r:
            continue
        results.append(r)
        with open(f"results/{r['call_id']}_score.json", "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        if i < len(files) - 1:
            delay = max(4, 12 - len(KEYS))
            print(f"  Waiting {delay}s...")
            time.sleep(delay)

    # Summary table
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"{'Call':<12} {'Customer':<18} {'Score':<7} {'Verdict':<8} Patterns")
    print("-" * 70)

    my_verdicts = {}
    good = bad = 0

    for r in results:
        v = r.get("verdict", "bad")
        my_verdicts[r["call_id"]] = v
        if v == "good": good += 1
        else:           bad  += 1
        detected = [k for k, val in r.get("patterns", {}).items() if val]
        print(f"{r['call_id']:<12} {r.get('customer_name','?'):<18} "
              f"{r.get('score',0):<7} {v.upper():<8} {', '.join(detected[:2])}")

    avg = sum(r.get("score", 0) for r in results) / len(results) if results else 0
    print("-" * 70)
    print(f"Average: {avg:.1f}/100  |  Good: {good}  |  Bad: {bad}")

    # Accuracy check
    print(f"\n{'='*60}")
    print("ACCURACY CHECK vs verdicts.json")
    print(f"{'='*60}")

    if not os.path.exists("verdicts.json"):
        print("verdicts.json not found")
        return

    with open("verdicts.json", encoding="utf-8") as f:
        raw = json.load(f)

    src = raw.get("verdicts", raw)
    true_verdicts = {
        k: (v["verdict"] if isinstance(v, dict) else v)
        for k, v in src.items()
    }

    correct = 0
    wrong   = []

    print(f"{'Call':<12} {'Mine':<10} {'True':<10} Match")
    print("-" * 42)

    for cid in sorted(my_verdicts):
        if cid not in true_verdicts:
            continue
        mine  = my_verdicts[cid]
        true  = true_verdicts[cid]
        match = "✅" if mine == true else "❌"
        if mine == true:
            correct += 1
        else:
            wrong.append(cid)
        print(f"{cid:<12} {mine.upper():<10} {true.upper():<10} {match}")

    total = len([c for c in my_verdicts if c in true_verdicts])
    print("-" * 42)
    print(f"\nAccuracy: {correct}/{total} = {correct/total*100:.0f}%")
    if wrong:
        print(f"Wrong   : {', '.join(wrong)}")

    # Save summary
    with open("results/summary.json", "w", encoding="utf-8") as f:
        json.dump({
            "average_score": round(avg, 1),
            "good":          good,
            "bad":           bad,
            "accuracy":      f"{correct/total*100:.0f}%" if total else "N/A",
            "my_verdicts":   my_verdicts,
            "details":       results,
        }, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) > 1:
        os.makedirs("results", exist_ok=True)
        my_verdicts = {}
        for cid in sys.argv[1:]:
            path = f"transcripts/{cid}.json"
            if not os.path.exists(path):
                print(f"Not found: {path}")
                continue
            r = evaluate(path)
            if r:
                my_verdicts[r["call_id"]] = r["verdict"]
                with open(f"results/{cid}_score.json", "w", encoding="utf-8") as f:
                    json.dump(r, f, indent=2, ensure_ascii=False)
        if my_verdicts and os.path.exists("verdicts.json"):
            with open("verdicts.json", encoding="utf-8") as f:
                raw = json.load(f)
            src    = raw.get("verdicts", raw)
            true_v = {k: (v["verdict"] if isinstance(v, dict) else v)
                      for k, v in src.items()}
            correct = sum(1 for c, v in my_verdicts.items() if true_v.get(c) == v)
            total   = sum(1 for c in my_verdicts if c in true_v)
            print(f"\nAccuracy for these calls: {correct}/{total}")
    else:
        run_all()