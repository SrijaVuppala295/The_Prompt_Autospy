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

## Part 2 — The Surgeon

### What's broken in system-prompt.md

Full analysis in `surgeon/flaw_analysis.md`. Summary:

| # | Flaw | Proof |
|---|---|---|
| 1 | No language switching rules — agent reverts to English after switching | call_02, call_07 |
| 2 | No already-paid escalation path — agent loops endlessly | call_03 |
| 3 | Urgency applied to everyone including grieving customers | call_02, call_03 |
| 4 | No callback opening phase — treats every callback as cold call | call_09 |
| 5 | No minimum exploration before closing — gives up after 1-2 turns | call_10 |
| 6 | Amount placeholders not filled — agent hallucinates wrong numbers | call_02, call_03, call_07 |

### Fixed prompt
See `system-prompt-fixed.md`. Changes are marked with `[FIX N]` so you can see exactly what was changed vs the original.

### Re-simulation

Feeds borrower messages from 3 bad calls into the LLM using the fixed prompt. Shows before (original broken agent) vs after (fixed agent).

```bash
python surgeon/resimulate.py
```

Calls re-simulated: call_02 (language), call_03 (already paid), call_09 (callback)

Results saved to `surgeon/`

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
