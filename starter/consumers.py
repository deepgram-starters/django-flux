"""WebSocket consumer for Flux - Raw WebSocket proxy to Deepgram"""
import os
import json
import asyncio
from urllib.parse import parse_qs, urlencode
from channels.generic.websocket import AsyncWebsocketConsumer
import websockets
import jwt
from dotenv import load_dotenv
from starter.views import SESSION_SECRET

load_dotenv()
API_KEY = os.environ.get("DEEPGRAM_API_KEY")
if not API_KEY:
    raise ValueError("DEEPGRAM_API_KEY required")

DEEPGRAM_STT_URL = "wss://api.deepgram.com/v2/listen"
DEFAULT_MODEL = "flux-general-en"


class FluxConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deepgram_ws = None
        self.forward_task = None
        self.stop_event = asyncio.Event()

    async def connect(self):
        """Accept WebSocket connection from client"""
        # Validate JWT from subprotocol
        protocols = self.scope.get("subprotocols", [])
        valid_proto = None
        for proto in protocols:
            if proto.startswith("access_token."):
                token = proto[len("access_token."):]
                try:
                    jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
                    valid_proto = proto
                except Exception:
                    pass
                break

        if not valid_proto:
            await self.close(code=4401)
            return

        await self.accept(subprotocol=valid_proto)
        print("Client connected to /api/flux")

        # Parse query parameters from scope
        query_string = self.scope.get('query_string', b'').decode('utf-8')
        params = parse_qs(query_string)

        model = DEFAULT_MODEL
        encoding = params.get('encoding', ['linear16'])[0]
        sample_rate = params.get('sample_rate', ['16000'])[0]
        eot_threshold = params.get('eot_threshold', [None])[0]
        eager_eot_threshold = params.get('eager_eot_threshold', [None])[0]
        eot_timeout_ms = params.get('eot_timeout_ms', [None])[0]
        keyterms = params.get('keyterm', [])

        # Build Deepgram WebSocket URL with parameters
        deepgram_params = {
            'model': model,
            'encoding': encoding,
            'sample_rate': sample_rate,
        }
        if eot_threshold:
            deepgram_params['eot_threshold'] = eot_threshold
        if eager_eot_threshold:
            deepgram_params['eager_eot_threshold'] = eager_eot_threshold
        if eot_timeout_ms:
            deepgram_params['eot_timeout_ms'] = eot_timeout_ms

        # Build URL with urlencode, then append multi-value keyterm params
        deepgram_url = f"{DEEPGRAM_STT_URL}?{urlencode(deepgram_params)}"
        for term in keyterms:
            deepgram_url += f"&keyterm={term}"

        print(f"Connecting to Deepgram Flux: model={model}, encoding={encoding}, sample_rate={sample_rate}")

        try:
            # Connect to Deepgram
            self.deepgram_ws = await websockets.connect(
                deepgram_url,
                additional_headers={"Authorization": f"Token {API_KEY}"}
            )
            print("Connected to Deepgram Flux API")

            # Start forwarding task
            self.forward_task = asyncio.create_task(self.forward_from_deepgram())

        except Exception as e:
            print(f"Error connecting to Deepgram: {e}")
            await self.send(text_data=json.dumps({
                "type": "Error",
                "description": str(e),
                "code": "CONNECTION_FAILED"
            }))
            await self.close(code=3000)

    async def disconnect(self, close_code):
        """Cleanup on disconnect"""
        print(f"Client disconnected: {close_code}")
        self.stop_event.set()

        if self.forward_task:
            self.forward_task.cancel()
            try:
                await self.forward_task
            except asyncio.CancelledError:
                pass

        if self.deepgram_ws:
            try:
                await self.deepgram_ws.close()
            except Exception as e:
                print(f"Error closing Deepgram connection: {e}")

    async def receive(self, text_data=None, bytes_data=None):
        """Forward messages from client to Deepgram"""
        if not self.deepgram_ws:
            return

        try:
            if text_data:
                await self.deepgram_ws.send(text_data)
            elif bytes_data:
                await self.deepgram_ws.send(bytes_data)
        except Exception as e:
            print(f"Error forwarding to Deepgram: {e}")
            await self.close(code=3000)

    async def forward_from_deepgram(self):
        """Forward messages from Deepgram to client"""
        try:
            async for message in self.deepgram_ws:
                if self.stop_event.is_set():
                    break

                # Forward binary or text messages
                if isinstance(message, bytes):
                    await self.send(bytes_data=message)
                else:
                    await self.send(text_data=message)

        except websockets.exceptions.ConnectionClosed as e:
            print(f"Deepgram connection closed: {e.code} {e.reason}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error forwarding from Deepgram: {e}")
            await self.send(text_data=json.dumps({
                "type": "Error",
                "description": str(e),
                "code": "PROVIDER_ERROR"
            }))
        finally:
            if not self.stop_event.is_set():
                await self.close(code=1000)
