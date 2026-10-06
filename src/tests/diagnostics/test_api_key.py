#!/usr/bin/env python3
"""
Test the current API key to see what type it is
"""

import os
import sys
import asyncio
import openai
from dotenv import load_dotenv

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
from spend_guard import require_spend  # noqa: E402  (spend gate: CUTTLE_ALLOW_SPEND=1)

# Load environment variables
load_dotenv()

async def test_current_api_key():
    """Test the current API key"""
    
    api_key = os.getenv('OPENAI_API_KEY')
    print(f"Current API key: {api_key[:20]}...")
    
    # Check if it's a placeholder value or test value
    if api_key == 'your_openai_api_key_here' or not api_key or api_key.startswith('your_') or api_key == 'test_key':
        print("[WARNING] Using placeholder/test API key - test will be skipped")
        print("   To test with real API key, update OPENAI_API_KEY in .env file")
        print("   Get your API key from: https://platform.openai.com/account/api-keys")
        return True  # Don't fail the test for placeholder values
    
    if api_key.startswith('sk-'):
        print("[PASS] This appears to be an OpenAI API key")
    else:
        print("[WARNING] Unknown API key format")
    
    # Try to use it — this sends a REAL prompt and spends REAL money.
    if not require_spend("a real gpt-3.5-turbo completion to validate the API key"):
        print("[SKIP] API key validity NOT checked (no spend without opt-in)")
        return True  # skipped, not failed
    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5
            ),
            timeout=10
        )
        print("[PASS] API key works!")
        return True
    except Exception as e:
        print(f"[FAIL] API key failed: {e}")
        return False

def run_api_key_tests():
    """Run API key tests and return results"""
    print("[TEST] Running API Key Tests...")
    print("=" * 50)
    
    try:
        # Run the async test
        result = asyncio.run(test_current_api_key())
        
        print(f"\n[RESULTS] API Key Test: {'PASS' if result else 'FAIL'}")
        return result
    except Exception as e:
        print(f"[FAIL] API Key test failed with error: {e}")
        return False

if __name__ == "__main__":
    success = run_api_key_tests()
    exit(0 if success else 1)
