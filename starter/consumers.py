"""WebSocket consumer for Flux — bridges the browser to Deepgram listen.v2 (Flux) via the SDK."""
import os
import json
import asyncio
from urllib.parse import parse_qs

import jwt
from channels.generic.websocket import AsyncWebsocketConsumer
from dotenv import load_dotenv

from deepgram import AsyncDeepgramClient
from deepgram.environment import DeepgramClientEnvironment
from deepgram.listen.v2.types import ListenV2CloseStream
from starter.views import SESSION_SECRET

load_dotenv()
API_KEY = os.environ.get("DEEPGRAM_API_KEY")
if not API_KEY:
    raise ValueError("DEEPGRAM_API_KEY required")

DEFAULT_MODEL = "flux-general-en"


# One async SDK client, reused across connections; the browser never sees the API key.
# DEEPGRAM_BASE_URL (e.g. wss://api.staging.deepgram.com) overrides the default
# production endpoint. listen.v2 uses environment.production for the /v2/listen ws.
def _build_client():
    base_url = os.environ.get("DEEPGRAM_BASE_URL")
    if base_url:
        https = base_url.replace("wss://", "https://").replace("ws://", "http://")
        env = DeepgramClientEnvironment(
            base=https, production=base_url, agent=base_url, agent_rest=https
        )
        print(f"Using custom Deepgram base URL: {base_url}")
        return AsyncDeepgramClient(api_key=API_KEY, environment=env)
    return AsyncDeepgramClient(api_key=API_KEY)


deepgram = _build_client()


class FluxConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connection = None
        self._connection_cm = None
        self.forward_task = None

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

        encoding = params.get('encoding', ['linear16'])[0]
        sample_rate = params.get('sample_rate', ['16000'])[0]
        eot_threshold = params.get('eot_threshold', [None])[0]
        eager_eot_threshold = params.get('eager_eot_threshold', [None])[0]
        eot_timeout_ms = params.get('eot_timeout_ms', [None])[0]
        keyterms = params.get('keyterm', [])

        connect_kwargs = {
            "model": DEFAULT_MODEL,
            "encoding": encoding,
            "sample_rate": sample_rate,
        }
        if eot_threshold:
            connect_kwargs["eot_threshold"] = eot_threshold
        if eager_eot_threshold:
            connect_kwargs["eager_eot_threshold"] = eager_eot_threshold
        if eot_timeout_ms:
            connect_kwargs["eot_timeout_ms"] = eot_timeout_ms
        if keyterms:
            connect_kwargs["keyterm"] = keyterms

        print(f"Connecting to Deepgram Flux: model={DEFAULT_MODEL}, encoding={encoding}, sample_rate={sample_rate}")

        try:
            # `connect()` is an async context manager; enter it manually so the
            # connection lives across the consumer's connect/disconnect lifecycle.
            self._connection_cm = deepgram.listen.v2.connect(**connect_kwargs)
            self.connection = await self._connection_cm.__aenter__()
            print("Connected to Deepgram Flux API")

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

        if self.forward_task:
            self.forward_task.cancel()
            try:
                await self.forward_task
            except asyncio.CancelledError:
                pass

        if self._connection_cm:
            try:
                await self._connection_cm.__aexit__(None, None, None)
            except Exception as e:
                print(f"Error closing Deepgram connection: {e}")

    async def receive(self, text_data=None, bytes_data=None):
        """Forward audio (binary) and control messages (JSON) from client to Deepgram."""
        if not self.connection:
            return

        try:
            if bytes_data:
                await self.connection.send_media(bytes_data)
            elif text_data:
                try:
                    data = json.loads(text_data)
                except (ValueError, TypeError):
                    print("Ignoring non-JSON message from client")
                    return
                if data.get("type") == "CloseStream":
                    await self.connection.send_close_stream(ListenV2CloseStream(type="CloseStream"))
                else:
                    print(f"Ignoring unknown client message type: {data.get('type')}")
        except Exception as e:
            print(f"Error forwarding to Deepgram: {e}")
            await self.close(code=3000)

    async def forward_from_deepgram(self):
        """Forward Deepgram messages to the browser: bytes as binary, models as JSON."""
        try:
            async for message in self.connection:
                if isinstance(message, (bytes, bytearray)):
                    await self.send(bytes_data=bytes(message))
                elif isinstance(message, dict):
                    # listen.v2 (Flux) yields plain dicts (e.g. TurnInfo); forward as-is
                    # so the transcript reaches the browser instead of {"type":"Unknown"}.
                    await self.send(text_data=json.dumps(message))
                elif hasattr(message, "model_dump_json"):
                    await self.send(text_data=message.model_dump_json())
                else:
                    await self.send(text_data=json.dumps(
                        {"type": getattr(message, "type", "Unknown")}
                    ))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error forwarding from Deepgram: {e}")
            try:
                await self.send(text_data=json.dumps({
                    "type": "Error",
                    "description": str(e),
                    "code": "PROVIDER_ERROR"
                }))
            except Exception:
                pass
        finally:
            try:
                await self.close(code=1000)
            except Exception:
                pass
