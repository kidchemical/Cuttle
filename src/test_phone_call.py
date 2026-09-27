#!/usr/bin/env python3
"""One-off test: place a VAPI outbound call to the given number."""
import os
import sys
from pathlib import Path

# Load .env from src/ or project root
src_dir = Path(__file__).resolve().parent
root_dir = src_dir.parent
for d in (src_dir, root_dir):
    env_file = d / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=True)
            print(f"[INFO] Loaded .env from {env_file}")
        except ImportError:
            pass
        break

# Ensure we can import from tools
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from tools.voice.voice_manager import make_phone_call

if __name__ == "__main__":
    # E.164: +1 + 707 + 5149247
    number = "+17075149247"
    goal = "Have a friendly short conversation. Introduce yourself as Cuttle's phone agent, ask how they're doing, and chat for a few back-and-forths. Only end when they say goodbye or want to hang up."
    context = {"test": "Cuttle phone agent"}

    print(f"Placing conversational test call to {number}...")
    result = make_phone_call(
        destination_number=number,
        goal=goal,
        context=context,
        call_name="Cuttle conversational test",
        conversational=True,
    )
    print("Success:", result.get("success"))
    print("Message:", result.get("message"))
    if result.get("call_id"):
        print("Call ID:", result["call_id"])
    if result.get("error"):
        print("Error:", result["error"])
