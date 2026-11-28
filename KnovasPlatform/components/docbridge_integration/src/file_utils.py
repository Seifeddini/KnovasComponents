"""
The AutoDoc root: where this deployment's documents are mounted.
"""

from typing import Optional


class AutoDocFileHandler:
    """The AutoDoc root (``autodoc.path``) the file routes resolve paths against."""

    def __init__(self, autodoc_path: Optional[str] = None):
        from config_loader import get_config
        config = get_config()

        self.autodoc_path = autodoc_path or config.get('autodoc.path', '/mnt/autodoc')
