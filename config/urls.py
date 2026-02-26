"""HTTP URL routing"""
from django.urls import path, include
from starter.views import serve_index, health

urlpatterns = [
    path('', serve_index, name='index'),
    path('health', health, name='health'),
    path('api/', include('starter.urls')),
]
