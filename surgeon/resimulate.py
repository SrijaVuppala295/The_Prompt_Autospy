"""
Part 2 - The Surgeon: Re-simulation

Takes 3 bad calls, feeds the borrower messages into an LLM
using the FIXED system prompt, and shows before/after:

BEFORE = original broken agent transcript (already in transcripts/)
AFTER  = new agent responses using fixed prompt

Calls:
- call_02: language switching failure
- call_03: already-paid loop with no escalation
- call_09: callback treated as cold call
"""

import os
import json
import time
import sys

print("Starting resimulate.py...")

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

print("Imports OK")


# ── Get Groq client ───────────────────────────────────────────────────────────
def get_client():
    for i in range(1, 10):
        key = os.getenv(f"GROQ_KEY_{i}")
        if key:
            return Groq(api_key=key)
    raise ValueError("No GROQ_KEY_1 found in .env file")


client = get_client()


# ── Helpers ───────────────────────────────────────────────────────────────────
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def fill_variables(prompt, customer):
    """Replace {{variable}} placeholders with actual customer values."""
    values = {
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
    for k, v in values.items():
        prompt = prompt.replace(k, str(v) if v else "N/A")
    return prompt

def get_customer_messages(transcript_data):
    """Get only the customer's lines from a transcript."""
    return [
        t["text"]
        for t in transcript_data.get("transcript", [])
        if t["speaker"] == "customer" and t["text"].strip()
    ]

def get_original_agent_messages(transcript_data):
    """Get only the original agent's lines from a transcript."""
    return [
        t["text"]
        for t in transcript_data.get("transcript", [])
        if t["speaker"] == "agent" and t["text"].strip()
    ]


# ── Simulate with fixed prompt ────────────────────────────────────────────────
def simulate_with_fixed_prompt(system_prompt, customer_messages, max_turns=10):
    """
    Feed customer messages one by one to the LLM using the fixed system prompt.
    Returns the simulated conversation.
    """
    history = []
    conversation = []

    for msg in customer_messages[:max_turns]:
        history.append({"role": "user", "content": msg})
        conversation.append({"speaker": "customer", "text": msg})

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    *history
                ],
                temperature=0.3,
                max_tokens=250,
            )
            reply = response.choices[0].message.content.strip()
        except Exception as e:
            reply = f"[API ERROR: {str(e)[:80]}]"
            print(f"  API error: {str(e)[:80]}")

        history.append({"role": "assistant", "content": reply})
        conversation.append({"speaker": "agent", "text": reply})
        time.sleep(2)  # stay within rate limits

    return conversation


# ── Run one call ──────────────────────────────────────────────────────────────
def run_one(call_id, fixed_prompt_path, flaw_being_fixed):
    print(f"\n{'='*65}")
    print(f"CALL: {call_id}")
    print(f"Flaw fixed: {flaw_being_fixed}")
    print(f"{'='*65}")

    # Load transcript
    data            = load_json(os.path.join("transcripts", f"{call_id}.json"))
    customer        = data.get("customer", {})
    customer_msgs   = get_customer_messages(data)
    original_agent  = get_original_agent_messages(data)

    print(f"Customer     : {customer.get('name')}")
    print(f"Disposition  : {data.get('disposition')}")
    print(f"Total turns  : {data.get('total_turns')}")

    # Load and prepare fixed prompt
    fixed_raw    = load_text(fixed_prompt_path)
    fixed_filled = fill_variables(fixed_raw, customer)
    system_prompt = fixed_filled[:3000]  # truncate to fit token limits

    # Run simulation with fixed prompt
    print(f"\nSimulating with fixed prompt (using first {min(10, len(customer_msgs))} customer turns)...")
    simulated = simulate_with_fixed_prompt(system_prompt, customer_msgs, max_turns=10)
    simulated_agent = [t["text"] for t in simulated if t["speaker"] == "agent"]

    # Print before/after
    print(f"\n--- BEFORE (original broken agent, first 4 responses) ---")
    for i, msg in enumerate(original_agent[:4]):
        print(f"  [{i+1}] {msg[:140]}")

    print(f"\n--- AFTER (fixed prompt simulation, first 4 responses) ---")
    for i, msg in enumerate(simulated_agent[:4]):
        print(f"  [{i+1}] {msg[:140]}")

    # Save full output
    output = {
        "call_id":          call_id,
        "flaw_fixed":       flaw_being_fixed,
        "customer":         customer.get("name"),
        "disposition":      data.get("disposition"),
        "before_original":  [{"turn": i+1, "text": t} for i, t in enumerate(original_agent)],
        "after_simulated":  simulated,
    }

    out_path = os.path.join("surgeon", f"{call_id}_before_after.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSaved: {out_path}")
    return output


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Running from:", os.getcwd())

    FIXED = "system-prompt-fixed.md"

    if not os.path.exists(FIXED):
        print(f"ERROR: {FIXED} not found in {os.getcwd()}")
        print("Make sure system-prompt-fixed.md is in your project root folder.")
        sys.exit(1)

    print(f"Found: {FIXED}")

    # Check API key
    found_key = any(os.getenv(f"GROQ_KEY_{i}") for i in range(1, 10))
    if not found_key:
        print("ERROR: No GROQ_KEY_1 found in .env file")
        sys.exit(1)

    print("API key OK")
    print("\nStarting re-simulations...\n")
    os.makedirs("surgeon", exist_ok=True)

    # 3 bad calls — one per major flaw
    calls = [
        ("call_02", "Language switching — agent kept reverting to English"),
        ("call_03", "Already paid — agent looped instead of escalating to email"),
        ("call_09", "Callback context — agent treated scheduled callback as cold call"),
    ]

    all_results = []
    for call_id, flaw in calls:
        result = run_one(call_id, FIXED, flaw)
        all_results.append(result)
        time.sleep(3)

    # Save combined summary
    summary_path = os.path.join("surgeon", "resimulation_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*65}")
    print("ALL DONE")
    print(f"{'='*65}")
    print("Files saved in surgeon/:")
    for call_id, _ in calls:
        print(f"  {call_id}_before_after.json")
    print("  resimulation_summary.json")