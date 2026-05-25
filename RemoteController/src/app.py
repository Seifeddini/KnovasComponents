"""Flask application factory for customer-hosted Remote Controller."""
from __future__ import annotations

import logging
import os

from flask import Flask

from config import load_config
from routes.discover import discover_bp
from routes.health import health_bp
from routes.metrics import metrics_bp
from routes.sync import sync_bp
from routes.sync_config_route import sync_config_bp
from routes.sync_control import sync_control_bp
from sync.sync_scheduler import maybe_auto_start

logger = logging.getLogger(__name__)


def create_app(*, skip_validation: bool = False) -> Flask:
    load_config(validate=not skip_validation)

    if os.environ.get("TESTING", "").strip().lower() not in ("1", "true", "yes", "on"):
        logging.basicConfig(
            level=logging.INFO,
            format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        )

    app = Flask(__name__)
    app.config["TESTING"] = False

    app.register_blueprint(health_bp)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(discover_bp)
    app.register_blueprint(sync_bp)
    app.register_blueprint(sync_control_bp)
    app.register_blueprint(sync_config_bp)

    @app.before_request
    def _log_request():
        pass

    with app.app_context():
        try:
            maybe_auto_start()
        except Exception as exc:
            logger.warning("Auto-start continuous sync skipped: %s", exc)

    return app


def _wsgi_skip_validation() -> bool:
    """Skip required-env validation only for tests or explicit dev override."""
    if os.environ.get("TESTING", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return os.environ.get("RC_SKIP_CONFIG_VALIDATION", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


app = create_app(skip_validation=_wsgi_skip_validation())


if __name__ == "__main__":
    cfg = load_config()
    app.run(host="0.0.0.0", port=cfg.rc_api_port, debug=False)
