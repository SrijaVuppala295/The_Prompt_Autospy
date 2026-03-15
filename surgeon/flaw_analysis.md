# Part 2 — Flaw Analysis

## What I Did
I read the system prompt carefully and matched it against the 5 bad calls.
Here are the problems I found and the proof from the transcripts.

---

## Flaw 1 — Agent Keeps Switching Back to English
**Found in: call_02, call_07**

The system prompt never tells the agent to stay in the customer's language.
The `switch_language` function exists but there's no rule saying
"once you switch, don't go back."

**What happened in call_02:**
The customer asked for Hindi 3 times. Each time the agent said okay,
then responded in English again in the very next message.

Customer said:
> "आप मुझे हिंदी में बात करिए. मैं बार बार request कर रही हूं"
> (Please speak Hindi. I am asking you again and again.)

Agent's next message: English again.

**What happened in call_07:**
Agent switched to Tamil when asked but then kept saying
"I don't understand what you're saying" over and over.
No fallback. No "let me get someone who speaks Tamil better."
Call went nowhere for 34 turns.

**The fix:** Add a clear rule — switch immediately, stay switched, and if it's
still not working, schedule a callback with a Tamil/Hindi speaking agent.

---

## Flaw 2 — Agent Has No Idea What to Do When Customer Says "I Already Paid"
**Found in: call_03**

The prompt tells the agent how to handle disputes like "I never took this loan."
But "I already paid" is not on the list. So the agent just keeps going
in circles saying "I can't find your payment."

**What happened in call_03:**
Customer gave a UTR number as payment proof.
Agent said "I cannot find this payment" — 4 times.
15 minutes. 105 turns. No resolution.

The agent never said:
- "Send proof to our email"
- "I'll flag this for our team to check"
- "I can't verify this on a call, here's what you should do"

It just looped. Customer got more and more frustrated.

**The fix:** Add a simple rule — if customer claims they already paid,
ask for UTR number, give them the support email, and end the call.
Don't keep pushing for payment.

---

## Flaw 3 — Agent Pushes Hard Even When It's Completely Wrong to Do So
**Found in: call_02, call_03**

The prompt says:
> "You MUST convey urgency. Failure to pay will result in serious consequences."
> "Remind them firmly: This is a pending obligation that requires immediate attention."

This instruction has no exceptions. So the agent applies it to everyone —
including a widow who just said her husband died last week.

**What happened in call_02:**
Customer said her husband died on March 7, 2025.
She's a housewife with no income.

Agent's response: talked about credit score damage and 668 days overdue.

That's the prompt's urgency rule working exactly as written.
It's just completely wrong for this situation.

**The fix:** Add a simple rule — if the customer mentions a death,
job loss, or genuine hardship, acknowledge it first.
Don't go straight to "you must pay."

---

## Flaw 4 — Agent Treats Every Call Like It's the First One
**Found in: call_09**

The system prompt has no instructions for callback calls —
calls where the customer asked to be called back.

When a customer requests a callback, they already know who's calling and why.
They don't need the full introduction again.
But the agent has no special instructions for this situation,
so it just uses the normal opening — full intro, full explanation.

**What happened in call_09:**
Customer's first words: "Yeah. Saturday evening also, you will call. Right?"
She knew it was a callback. She remembered the previous conversation.

Agent completely ignored this and launched into the full cold-call intro.
Then the connection dropped and the agent just said "Goodbye."
No "I'll call you right back." No rescheduling. Nothing.

**The fix:** Add a callback opening phase — acknowledge the previous call,
skip the intro, and if connection drops, immediately try to reschedule.

---

## Flaw 5 — Agent Gives Up Too Fast
**Found in: call_10**

The prompt says after 5-6 circular exchanges, move to closing.
But it never says you must try at least a few different angles first.

So if a customer gives short or confusing answers,
the agent can just move to closing after 1-2 exchanges.

**What happened in call_10:**
Customer gave one confused response.
Agent asked one vague question, got a short reply,
and immediately wrapped up: "Would it be alright if I checked in next week?"

9 turns. 110 seconds. Agent barely tried.

**The fix:** Add a minimum — the agent must ask at least 3 different
questions before moving on. Try employment, income, timeline, family support.
Don't give up after one confused exchange.

---

## Flaw 6 — Wrong Amounts Injected Every Call (Proves: call_02, call_03, call_07)

### What's broken
The system prompt uses template variables like `{{tos}}`, `{{pos}}`, `{{settlement_amount}}`
to inject the customer's actual loan amounts. But these variables are not being
filled correctly before the prompt reaches the agent.

As a result, the agent either:
- Gets hallucinated/wrong numbers from the LLM
- Gets zero/empty values and makes up amounts
- Gets amounts from a different customer's record

### Evidence: call_02 (Rahul Verma)
Customer data says pending_amount = "fifty thousand"
Agent says: "अट्ठाईस हज़ार पाँच सौ बयासी रुपये" (28,582 rupees)
Wrong amount. Never corrected.

### Evidence: call_03 (Anjali Reddy)
Customer data says pending_amount = "fifty thousand", closure_amount = "thirty five thousand"
Agent says: "दस हज़ार एक सौ रुपये" (10,100 rupees) as the closure amount
Completely wrong figure. Customer is confused throughout.

### Evidence: call_07 (Meera Joshi)
Customer data says pending_amount = "fifty thousand"
Agent says: "ஐம்பத்து ஐந்தாயிரத்து முன்னூற்று முப்பத்தி ஐந்து ரூபாய்" (55,335 rupees)
Different wrong number again.

### Root cause
The `{{tos}}`, `{{pos}}`, `{{settlement_amount}}` placeholders in the prompt
are not being filled by the system before the agent sees the prompt.
The LLM then fills in the blanks by hallucinating plausible-sounding numbers.

---

## Summary

| # | Problem | Calls affected |
|---|---------|----------------|
| 1 | No rule to stay in customer's language | call_02, call_07 |
| 2 | No process when customer says "I already paid" | call_03 |
| 3 | Urgency applied even in completely wrong situations | call_02, call_03 |
| 4 | No callback opening — treats every call as a cold call | call_09 |
| 5 | Agent gives up too early without really trying | call_10 |
| 6 | Wrong amounts injected every call | call_02, call_03, call_07 |