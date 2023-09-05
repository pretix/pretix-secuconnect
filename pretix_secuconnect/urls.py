from django.urls import include, path

from .views import ReturnView, redirect_view

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
            ]
        ),
    ),
]
