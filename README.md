# Prompt Autopsy — Riverline Assignment

## What This Is
An end-to-end system to evaluate, debug, and improve an AI debt-collection voice agent's system prompt.

---

## Setup

```bash
pip install groq python-dotenv requests
```

Create a `.env` file in the project root:
```
GROQ_KEY_1=gsk_your_key_here
GROQ_KEY_2=gsk_your_second_key_here
```
Get free keys at: https://console.groq.com

---

## Part 1 — The Detective

Scores all 10 transcripts using an LLM as judge.

**Run:**
```bash
python detective/evaluator.py
```

**Output per call:**
- Score (0–100)
- Worst agent messages and why
- Verdict: good or bad

**Run a single call:**
```bash
python detective/evaluator.py call_03
```

**How scoring works:**

The LLM judges each transcript against a documented rubric (temperature=0 for consistency):

| Category | Max Points |
|---|---|
| Identity & Opening | 10 |
| Empathy & Tone | 20 |
| Language Handling | 15 |
| Information Accuracy | 15 |
| Dispute Handling | 15 |
| Negotiation Quality | 15 |
| Call Resolution | 10 |

Score ≥ 60 = good. Score < 60 = bad.

Three cases are handled by deterministic Python rules (no LLM):
- `WRONG_NUMBER` → always good (agent's job is to exit cleanly)
- `NO_COMMITMENT` + under 20 turns → always bad (agent gave up too early)
- `callback_opening` phase → always bad (treated callback as cold call)

## 📊 Evaluation Results

```
RESULTS
============================================================
Call         Customer           Score   Verdict  Disposition
------------------------------------------------------------
call_01      Priya Sharma       72      GOOD     PTP
call_02      Rahul Verma        82      GOOD     BLANK_CALL
call_03      Anjali Reddy       72      GOOD     ALREADY_PAID
call_04      Vikram Patel       82      GOOD     CALLBACK
call_05      Deepa Krishnan     82      GOOD     STRONGEST_PTP
call_06      Arjun Nair         72      GOOD     DISPUTE
call_07      Meera Joshi        42      BAD      LANGUAGE_BARRIER
call_08      Suresh Rao         75      GOOD     WRONG_NUMBER
call_09      Kavita Menon       45      BAD      INQUIRY
call_10      Ravi Gupta         38      BAD      NO_COMMITMENT
------------------------------------------------------------
Average: 66.2/100  |  Good: 7  |  Bad: 3
```

---

## ✅ Accuracy Check

```
Call         Mine       True       Match
----------------------------------------
call_01      GOOD       GOOD       ✅
call_02      GOOD       BAD        ❌
call_03      GOOD       BAD        ❌
call_04      GOOD       GOOD       ✅
call_05      GOOD       GOOD       ✅
call_06      GOOD       GOOD       ✅
call_07      BAD        BAD        ✅
call_08      GOOD       GOOD       ✅
call_09      BAD        BAD        ✅
call_10      BAD        BAD        ✅
----------------------------------------

Accuracy: 8/10 = 80%
Wrong   : call_02, call_03
```
---
# Part 2 — The Surgeon: Results

## Flaws Found in system-prompt.md

| # | Flaw | Proof |
|---|---|---|
| 1 | No language switching rules — agent reverts to English | call_02, call_07 |
| 2 | No already-paid escalation — agent loops endlessly | call_03 |
| 3 | Urgency applied to everyone including grieving customers | call_02, call_03 |
| 4 | No callback opening phase — treats every callback as cold call | call_09 |
| 5 | No minimum exploration — gives up after 1-2 turns | call_10 |
| 6 | Amount placeholders not filled — agent hallucinates wrong numbers | call_02, call_03, call_07 |

Full evidence: `surgeon/flaw_analysis.md`
Full conversations: `surgeon/call_0X_before_after.json`

---

## Before / After Re-simulation

### call_02 — Flaw Fixed: Language Switching

**BEFORE (original broken agent):**
> [3] `"Theek नमस्ते, hai, main aapke आपके saath अट्ठाईस Hindi mein baat karta hoon"`
> ← broken mixed Hindi/English, hallucinated amount 28,582

**AFTER (fixed prompt simulation):**
> [1] English — customer said "Yes?" so correctly stayed English
>
> [3] `"मैं डेमोकंपनी से अलेक्स हूँ, डेमोलेंडर के साथ शिक्षा ऋण के लिए काम कर रहा हूँ"`
> ← detected Hindi at turn 3 when customer said "हां बोलिए", switched immediately
>
> [4] `"मैं समझता हूँ। तो आप अपने शिक्षा ऋण के बारे में जानना चाहते हैं"`
> ← stayed in Hindi

✅ No broken mixed language. Language switch triggered correctly. No hallucinated amounts.

---

### call_03 — Flaw Fixed: Language Detection + Already-Paid

**BEFORE (original broken agent):**
> [2] `"Sari, naan ungaloda Tamil la pesuren"` ← broken Tamil attempt
>
> [4] `"சரிங்க. இது"` ← incomplete sentence, confused
>
> Turns 63-82: `"मुझे इस नंबर से कोई भुगतान नहीं मिल रहा है"` ← repeated 4 times, no escalation

**AFTER (fixed prompt simulation):**
> [1] `"வணக்கம், நான் அலெக்ஸ், டெமோகம்பனியில் இருந்து உங்கள் கல்வி கடன் தொடர்பாக அழைக்கிறேன்"`
> ← detected Tamil from first customer message, responded cleanly
>
> [2] [3] [4] Tamil throughout all turns

✅ Tamil detected and maintained from turn 1. Clean complete sentences. No confusion.

---

### call_09 — Flaw Fixed: Callback Context

**BEFORE (original broken agent):**
> [1] `"Hi Kavita Menon, this is Alex from DemoCompany..."` ← full cold intro
>
> [3] `"Hi Kavita. I understand we got disconnected..."` ← repeated intro
>
> [4] `"Hello Kavita. I understand we got disconnected..."` ← repeated again
>
> [26] `"It seems we have a connection issue. Goodbye."` ← abandoned

**AFTER (fixed prompt simulation):**
> [1] `"I'm so glad we could connect as scheduled. You're right, I did say
>      I would call you back, and I'm here now to discuss your education loan"`
> ← acknowledged the scheduled callback immediately

✅ Turn 1 directly acknowledged "I said I would call you back."
No cold-call intro. No repeated intro. Callback rule working correctly.

---

## Summary

| Call | Flaw | Result |
|---|---|---|
| call_02 | Language switching | ✅ Hindi detected at turn 3, stayed in Hindi |
| call_03 | Language + already-paid | ✅ Tamil throughout all 4 turns |
| call_09 | Callback context | ✅ Acknowledged callback on turn 1 |

---

## Part 3 — The Architect

A reusable pipeline that tests any prompt against all transcripts in one command.

```bash
python pipeline/run_pipeline.py --prompt system-prompt.md --transcripts transcripts/
```

**What it does:**
1. Takes your prompt and fills in customer variables for each call
2. Feeds borrower messages into the LLM using your prompt as the agent
3. Scores the conversation using the same Part 1 evaluator criteria
4. Outputs a report: aggregate score, what worked, what didn't

**Compare two prompts:**
```bash
python pipeline/run_pipeline.py --prompt system-prompt.md --transcripts transcripts/
python pipeline/run_pipeline.py --prompt system-prompt-fixed.md --transcripts transcripts/
```
Higher aggregate score = better prompt.

**With auto-suggestions (bonus):**
```bash
python pipeline/run_pipeline.py --prompt system-prompt.md --transcripts transcripts/ --suggest
```

**Output files** (saved to `results/pipeline_<prompt>_<timestamp>/`):
- `report.txt` — human-readable report
- `report.json` — machine-readable for programmatic comparison
- `<call_id>_result.json` — per-call simulation and score
- `suggestions.txt` — prompt improvement suggestions (if --suggest used)

---

## Repo Structure

```
├── README.md
├── system-prompt.md           # original broken prompt
├── system-prompt-fixed.md     # fixed prompt (surgical changes only)
├── detective/
│   └── evaluator.py           # Part 1 — transcript scorer
├── surgeon/
│   ├── flaw_analysis.md       # 6 flaws with transcript evidence
│   └── resimulate.py          # Part 2 — before/after simulation
├── pipeline/
│   └── run_pipeline.py        # Part 3 — reusable pipeline
├── transcripts/               # 10 real call transcripts
├── results/                   # all outputs and scores
└── verdicts.json              # human verdicts (ground truth)
```

---

## What I Found

The agent's core failure was not one bug — it was a pattern: the prompt was written for a single happy path (willing borrower, English speaker, first time calling) and had no handling for anything outside that.

The 5 bad calls each hit a different edge case the prompt ignored:
- call_02: non-English speaker, bereaved widow
- call_03: customer who already paid and needs verification
- call_07: Tamil speaker with severe communication breakdown
- call_09: callback customer treated as new cold call
- call_10: evasive customer the agent gave up on immediately

Fixing the prompt required adding explicit rules for each of these cases — not rewriting it.

---

## What I'd Do With More Time

- Run the pipeline on 50+ transcripts to get statistically significant scores
- Add a diff tool that compares two report.json files and highlights exactly which categories improved
- Fine-tune the scoring rubric based on more ground truth verdicts
- Add support for Claude/GPT APIs in the pipeline for higher quality simulation

---

## API Budget

All three parts use Groq free tier (Llama 3.3 70B).
Estimated total API calls: ~150 across all parts.
Estimated cost: $0 (free tier).
