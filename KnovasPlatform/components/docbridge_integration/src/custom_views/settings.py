"""Settings of the custom views feature, read from config.yaml / environment only."""

import logging
from dataclasses import dataclass, fields

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CustomViewsSettings:
    enabled: bool = False
    dir: str = "/mnt/custom-views"
    facts_per_minute: int = 60
    max_nodes: int = 200
    rescan_seconds: int = 30
    file_max_bytes: int = 524288
    view_max_bytes: int = 2097152
    max_views: int = 50


def _int(config, name: str, default: int) -> int:
    key = f"custom_views.{name}"
    raw = config.get(key)
    if raw is None:
        return default
    try:
        int(raw)
    except (ValueError, TypeError):
        logger.warning("%s is not a number (%r); using %s", key, raw, default)
        return default
    return config.get_int(key, default)


def load_settings(config) -> CustomViewsSettings:
    """Read ``custom_views.*``; a value that does not parse keeps its default."""
    defaults = CustomViewsSettings()
    values = {}
    for field in fields(CustomViewsSettings):
        default = getattr(defaults, field.name)
        if field.name == "enabled":
            values[field.name] = config.get_bool("custom_views.enabled", default)
        elif field.name == "dir":
            raw = config.get("custom_views.dir", default)
            values[field.name] = str(raw) if raw else default
        else:
            values[field.name] = _int(config, field.name, default)
    return CustomViewsSettings(**values)
