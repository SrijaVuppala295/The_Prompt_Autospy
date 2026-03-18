# Part 2 — Flaw Analysis

## What I Did
Read the system prompt carefully and matched each broken instruction
against the 5 bad calls to find the exact cause of each failure.

---

## Flaw 1 — No Language Switching Rules
**Calls affected: call_02, call_07**

The system prompt has a `switch_language` function but zero instructions
on when to call it, how fast to switch, or what to do after switching.

**Evidence from call_02:**
Customer said in turn 8:
> "अभी अभिषेक जी पहले आप मुझे हिंदी में बात करिए. मैं बार बार request कर रही हूं"
> (Please speak Hindi. I am asking you again and again.)

Agent's next response — back to English:
> "I am so sorry to hear about your husband's passing, Rahul..."

Customer asked 3 more times. Agent kept reverting to English.

**Evidence from call_07:**
Agent switched to Tamil when asked but conversation immediately broke down.
No fallback — no "let me arrange a Tamil-speaking agent for you."
34 turns of back-and-forth with zero progress.

**Root cause in prompt:**
The original prompt had no rule like "once you switch, stay switched."
The `switch_language` function existed but was never triggered properly.

**Fix added:** Detect language from ANY response — not just first.
Switch immediately when customer speaks Hindi/Tamil. Never revert.
If communication still fails, schedule callback with appropriate agent.

---

## Flaw 2 — No Already-Paid Escalation Path
**Call affected: call_03**

The prompt handles disputes for "I never took this loan" but
"I already paid" is not on the list. Agent fell through to
normal discovery flow and looped endlessly.

**Evidence from call_03:**
Customer gave UTR number CM552522 as payment proof.
Agent said "I cannot find this payment" — 4 times in a row.
15 minutes. 105 turns. No email given. No escalation. No resolution.

The agent never said:
- "Send proof to support@demolender.com"
- "I'll flag this for verification"
- "I cannot verify on a call, here's what to do next"

**Root cause in prompt:**
DISPUTE DETECTION listed only: "I never took this loan",
"institute shut down", "I was promised cancellation."
"I already paid" was missing — so no escalation path triggered.

**Fix added:** If customer claims already paid at any point:
ask for UTR number, provide support@demolender.com, end call cleanly.
Do NOT say "I cannot find this payment" more than once.

---

## Flaw 3 — Urgency Applied to Everyone Including Grieving Customers
**Calls affected: call_02, call_03**

The global system prompt said:
> "You MUST convey urgency. The borrower needs to understand that
> failure to pay will result in serious consequences."
> "Remind them firmly: This is a pending obligation that requires
> immediate attention."

No exceptions. Agent applied this to everyone.

**Evidence from call_02:**
Customer said her husband died on March 7, 2025.
She is a housewife with no income.

Agent's response:
> "even though there's a dispute, at six hundred and sixty-eight
> days past due, every month adds another negative entry to
> the credit report"

Agent was following the prompt exactly — the prompt was wrong.

**Root cause in prompt:**
Urgency instruction had no exceptions for hardship, death,
already-paid claims, or callback customers.

**Fix added:** Default tone is calm and helpful — NOT urgent.
Urgency only for willing borrowers delaying without reason.
Never use urgency when: death in family, job loss, already paid,
or scheduled callback.

---

## Flaw 4 — No Callback Opening Phase
**Call affected: call_09**

The prompt has Opening, Discovery, Negotiation, Closing phases.
Zero instructions for callback calls — calls where the customer
requested to be called back.

**Evidence from call_09:**
Customer's first words: "Yeah. Saturday evening also, you will call. Right?"
She knew it was a callback. She remembered the previous conversation.

Agent completely ignored this and launched into the full cold-call intro.
Then repeated the intro 3 more times. When connection dropped —
agent said "Goodbye" and hung up. No recovery. No reschedule.

**Root cause in prompt:**
No callback_opening phase instructions. Agent defaulted to
cold call Opening phase every time regardless of context.

**Fix added:** New callback rule — if customer references a previous
call or scheduled callback, skip the intro, acknowledge the prior
interaction, pick up from where you left off.
If connection drops during a callback, immediately reschedule.

---

## Flaw 5 — No Minimum Exploration Before Closing
**Call affected: call_10**

The prompt says "after 5-6 circular exchanges, move to closing."
No minimum — agent can exit after 1-2 exchanges if customer
is evasive or gives short answers.

**Evidence from call_10:**
Customer gave one confused response.
Agent asked one vague question, got a short reply, and
immediately wrapped up:
> "Would it be alright if I checked in with you in about a week?"

9 turns. 110 seconds. Agent barely tried.

**Root cause in prompt:**
No rule requiring minimum exploration before moving on.
No instruction to try different angles if borrower is evasive.

**Fix added:** Must ask at least 3 different questions before
moving on. Topics: employment, income, timeline, family support.
Try different angles — don't give up after one short response.

---

## Flaw 6 — Amount Placeholders Not Filled Correctly
**Calls affected: call_02, call_03, call_07**

The prompt uses `{{tos}}`, `{{pos}}`, `{{settlement_amount}}`
to inject customer amounts. These placeholders were not being
filled before reaching the agent. The LLM hallucinated numbers.

**Evidence:**
- call_02: customer data = 50,000 | agent said 28,582
- call_03: customer data = 50,000 | agent said 10,100
- call_07: customer data = 50,000 | agent said 55,335

Every bad call had a different wrong number.

**Root cause:**
The variable injection pipeline was broken — placeholders not filled.
LLM saw `{{tos}}` literally or empty and made up a plausible number.

**Fix added:** Amount validation rule — if amounts look unfilled,
say "Let me pull up your exact figures" instead of guessing.
Never state an amount you are not certain of.

---

## Summary

| # | Flaw | Calls | Fixed |
|---|---|---|---|
| 1 | No language switching rules | call_02, call_07 | ✅ |
| 2 | No already-paid escalation | call_03 | ✅ |
| 3 | Urgency applied to everyone | call_02, call_03 | ✅ |
| 4 | No callback opening phase | call_09 | ✅ |
| 5 | No minimum exploration | call_10 | ✅ |
| 6 | Amount placeholders not filled | call_02, call_03, call_07 | ✅ |

All fixes are marked `[FIX N]` in `system-prompt-fixed.md`.