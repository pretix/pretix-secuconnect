from django.urls import include, path
from pretix.multidomain import event_path

from .views import ReturnView, WebhookView, redirect_view

event_patterns = [
    path(
        "secuconnect/",
        include(
            [
                path("redirect/", redirect_view, name="redirect"),
                path(
                    "return/<str:order>/<str:hash>/<int:payment>/<str:action>",
                    ReturnView.as_view(),
                    name="return",
                ),
                event_path(
                    "webhook/<str:order>/<str:hash>/<int:payment>/<str:action>",
                    WebhookView.as_view(),
                    name="webhook",
                    require_live=False,
                ),
            ]
        ),
    ),
]
