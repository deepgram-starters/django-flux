"""
Django Flux Starter - Application Entry Point

Launches the Daphne ASGI server for Django Channels.
This file provides a simple entry point for the deepgram.toml start command.

Routes:
  GET  /api/session              - Issue JWT session token
  GET  /api/metadata             - Project metadata from deepgram.toml
  WS   /api/flux                 - WebSocket proxy to Deepgram Flux (auth required)
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Validate required environment variables
if not os.environ.get("DEEPGRAM_API_KEY"):
    print("\n" + "=" * 70)
    print("ERROR: Deepgram API key not found!")
    print("=" * 70)
    print("\nPlease set your API key using one of these methods:")
    print("\n1. Create a .env file (recommended):")
    print("   DEEPGRAM_API_KEY=your_api_key_here")
    print("\n2. Environment variable:")
    print("   export DEEPGRAM_API_KEY=your_api_key_here")
    print("\nGet your API key at: https://console.deepgram.com")
    print("=" * 70 + "\n")
    sys.exit(1)

port = int(os.environ.get("PORT", 8081))
host = os.environ.get("HOST", "0.0.0.0")

print("\n" + "=" * 70)
print("Django Flux Server (Backend API)")
print("=" * 70)
print(f"Server:   http://{host}:{port}")
print("")
print("GET  /api/session")
print("WS   /api/flux (auth required)")
print("GET  /api/metadata")
print("=" * 70 + "\n")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Launch Daphne ASGI server
from daphne.cli import CommandLineInterface
sys.argv = ["daphne", "-b", host, "-p", str(port), "config.asgi:application"]
CommandLineInterface().run()
