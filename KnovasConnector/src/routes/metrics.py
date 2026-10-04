from flask import Blueprint, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

metrics_bp = Blueprint("metrics", __name__)


@metrics_bp.route("/metrics", methods=["GET"])
def prometheus_metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
