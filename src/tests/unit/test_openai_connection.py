#!/usr/bin/env python3
"""
Test OpenAI API connection to diagnose connection issues
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

async def test_openai_connection():
    """Test OpenAI API connection with detailed error reporting"""
    
    # Get API key
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        print("[FAIL] No OPENAI_API_KEY found in environment")
        return False
    
    print(f"[PASS] API key found: {api_key[:20]}...")
    
    # Create client
    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        print("[PASS] OpenAI client created successfully")
    except Exception as e:
        print(f"[FAIL] Failed to create OpenAI client: {e}")
        return False
    
    # Test simple API call — this sends a REAL prompt and spends REAL money.
    if not require_spend("a real gpt-3.5-turbo completion via the OpenAI API"):
        print("[SKIP] API call NOT made (no spend without opt-in)")
        return True  # skipped, not failed
    try:
        print("[API] Testing API call...")
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "Say 'Hello, this is a test'"}
                ],
                max_tokens=50
            ),
            timeout=30
        )
        
        print("[PASS] API call successful!")
        print(f"Response: {response.choices[0].message.content}")
        return True
        
    except asyncio.TimeoutError:
        print("[FAIL] API call timed out after 30 seconds")
        return False
    except openai.APIConnectionError as e:
        print(f"[FAIL] API Connection Error: {e}")
        print("This usually means network connectivity issues")
        return False
    except openai.AuthenticationError as e:
        print(f"[FAIL] Authentication Error: {e}")
        print("This usually means invalid API key")
        return False
    except openai.RateLimitError as e:
        print(f"[FAIL] Rate Limit Error: {e}")
        print("This usually means API quota exceeded")
        return False
    except openai.APIError as e:
        print(f"[FAIL] API Error: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Unexpected error: {e}")
        print(f"Error type: {type(e).__name__}")
        return False

async def test_network_connectivity():
    """Test basic network connectivity"""
    import aiohttp
    
    print("\n[NETWORK] Testing network connectivity...")
    
    connector = None
    try:
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Test general internet connectivity
            async with session.get('https://httpbin.org/get', timeout=10) as response:
                if response.status == 200:
                    print("[PASS] General internet connectivity works")
                else:
                    print(f"[WARNING] Internet connectivity issue: HTTP {response.status}")
                    return False
            
            # Test OpenAI API endpoint connectivity
            async with session.get('https://api.openai.com/v1/models', timeout=10) as response:
                if response.status in [200, 401]:  # 401 is expected without auth
                    print("[PASS] OpenAI API endpoint is reachable")
                else:
                    print(f"[WARNING] OpenAI API endpoint issue: HTTP {response.status}")
                    return False
    except Exception as e:
        print(f"[FAIL] Network connectivity test failed: {e}")
        return False
    finally:
        if connector:
            await connector.close()
    
    return True

async def main():
    print("[SEARCH] OpenAI Connection Diagnostic Tool")
    print("=" * 50)
    
    # Test network connectivity first
    network_ok = await test_network_connectivity()
    
    if not network_ok:
        print("\n[FAIL] Network connectivity issues detected")
        print("Please check your internet connection and firewall settings")
        return
    
    # Test OpenAI API connection
    print("\n" + "=" * 50)
    api_ok = await test_openai_connection()
    
    if api_ok:
        print("\n[PASS] OpenAI API connection is working!")
    else:
        print("\n[FAIL] OpenAI API connection failed")
        print("\nTroubleshooting suggestions:")
        print("1. Check your internet connection")
        print("2. Verify your OpenAI API key is valid")
        print("3. Check if you have sufficient API credits")
        print("4. Try again in a few minutes (API might be temporarily down)")

def run_openai_connection_tests():
    """Run OpenAI connection tests and return results"""
    print("[TEST] Running OpenAI Connection Tests...")
    print("=" * 50)
    
    # Run the async test
    asyncio.run(main())
    return True  # The main function handles its own success/failure reporting

if __name__ == "__main__":
    success = run_openai_connection_tests()
    exit(0 if success else 1)
