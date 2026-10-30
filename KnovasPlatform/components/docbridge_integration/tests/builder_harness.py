"""The builder blueprint on a bare Flask app, for tests and the click-dummy.

No identity database: the gate is a stand-in that returns whichever user the
caller hands it, which is all ``create_builder_blueprint`` asks of a gate.
``scripts/builder_preview.py`` serves the same app for screenshots.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from flask import Flask

from web_interface.builder_routes import create_builder_blueprint

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"


class StandInUser:
    def __init__(self, roles=frozenset(), display_name="Alex Beispiel", email="alex@example.org"):
        self.id = uuid.uuid4()
        self.roles = frozenset(roles)
        self.display_name = display_name
        self.email = email


class StandInGate:
    def __init__(self, user=None):
        self.user = user

    def current_user(self):
        return self.user


def make_app(user=None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(WEB / "templates"),
        static_folder=str(WEB / "static"),
    )
    app.testing = True

    @app.route("/login")
    def login():
        return "login"

    @app.route("/")
    def index():
        return "platform"

    app.register_blueprint(create_builder_blueprint(
        StandInGate(user),
        page_context=lambda: {
            "app_title": "Knovas",
            "brand": "Knovas",
            "asset_version": "test",
            "platform_url": "/",
        },
    ))
    return app
