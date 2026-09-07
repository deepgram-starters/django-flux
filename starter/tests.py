"""Regression tests for the Flux-to-Deepgram WebSocket bridge."""
import json
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DEEPGRAM_API_KEY", "test-api-key")

import jwt
from deepgram.core.api_error import ApiError

from starter.consumers import FluxConsumer
from starter.views import SESSION_SECRET


class BlockingSocket:
    """A socket that remains open until the consumer cancels its forward task."""

    def __init__(self):
        self.media = []
        self.close_streams = []
        self._wait = None

    async def send_media(self, media):
        self.media.append(media)

    async def send_close_stream(self, close_stream):
        self.close_streams.append(close_stream)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._wait is None:
            import asyncio

            self._wait = asyncio.Event()
        await self._wait.wait()
        raise StopAsyncIteration


class MessageSocket:
    def __init__(self, messages=(), error=None):
        self._messages = iter(messages)
        self._error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._error:
            raise self._error
        try:
            return next(self._messages)
        except StopIteration as error:
            raise StopAsyncIteration from error


class FakeConnectionContext:
    def __init__(self, socket):
        self.socket = socket
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.socket

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True


class FakeListenV2:
    def __init__(self, context):
        self.context = context
        self.connect_kwargs = None

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        return self.context


class FluxConsumerTests(IsolatedAsyncioTestCase):
    def make_consumer(self):
        consumer = FluxConsumer()
        consumer.accept = AsyncMock()
        consumer.send = AsyncMock()
        consumer.close = AsyncMock()
        return consumer

    async def test_sdk_connection_forwards_audio_and_close_stream(self):
        socket = BlockingSocket()
        context = FakeConnectionContext(socket)
        listen = FakeListenV2(context)
        client = SimpleNamespace(listen=SimpleNamespace(v2=listen))
        token = jwt.encode({}, SESSION_SECRET, algorithm="HS256")
        consumer = self.make_consumer()
        consumer.scope = {
            "subprotocols": [f"access_token.{token}"],
            "query_string": b"eot_threshold=0.7&keyterm=Deepgram&keyterm=Flux",
        }

        with patch("starter.consumers.deepgram", client):
            await consumer.connect()
            await consumer.receive(bytes_data=b"audio")
            await consumer.receive(text_data='{"type":"CloseStream"}')
            await consumer.disconnect(1000)

        self.assertTrue(context.entered)
        self.assertTrue(context.exited)
        self.assertEqual(socket.media, [b"audio"])
        self.assertEqual(socket.close_streams[0].type, "CloseStream")
        self.assertEqual(listen.connect_kwargs["keyterm"], ["Deepgram", "Flux"])

    async def test_dict_turn_info_reaches_the_browser(self):
        message = {"type": "TurnInfo", "event": "Update", "transcript": "hello"}
        consumer = self.make_consumer()
        consumer.connection = MessageSocket([message])

        await consumer.forward_from_deepgram()

        sent_message = consumer.send.await_args.kwargs["text_data"]
        self.assertEqual(json.loads(sent_message), message)

    async def test_provider_errors_do_not_expose_authorization(self):
        secret = "Token test-api-key"
        consumer = self.make_consumer()
        consumer.connection = MessageSocket(
            error=ApiError(status_code=400, headers={"Authorization": secret}, body="bad request")
        )

        with patch("builtins.print") as print_mock:
            await consumer.forward_from_deepgram()

        frame = consumer.send.await_args.kwargs["text_data"]
        self.assertNotIn(secret, frame)
        self.assertNotIn(secret, str(print_mock.call_args_list))
        self.assertEqual(json.loads(frame)["description"], "Deepgram rejected the connection (HTTP 400)")
