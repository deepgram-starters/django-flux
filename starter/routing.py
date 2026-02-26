"""WebSocket URL routing"""
from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path('api/flux', consumers.FluxConsumer.as_asgi()),
]
