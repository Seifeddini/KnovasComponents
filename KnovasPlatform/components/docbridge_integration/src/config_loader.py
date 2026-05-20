"""
Configuration loader for DocBridge-Knovas integration.
Loads and validates configuration from YAML file and environment variables.
"""

import os
import yaml
from typing import Any, Dict, Optional
from pathlib import Path
import re


class ConfigLoader:
    """Load and manage configuration for DocBridge integration."""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration loader.
        
        Args:
            config_path: Path to configuration YAML file.
                        If None, searches in default locations.
        """
        if config_path is None:
            config_path = self._find_config_file()
        
        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self._load_config()
    
    def _find_config_file(self) -> str:
        """Find configuration file in default locations."""
        possible_paths = [
            os.path.join(os.getcwd(), "config", "config.yaml"),
            os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml"),
            "/app/config/config.yaml",
            os.path.expanduser("~/.docbridge/config.yaml"),
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        raise FileNotFoundError(
            f"Configuration file not found in any of: {possible_paths}"
        )
    
    def _load_config(self):
        """Load configuration from YAML file."""
        with open(self.config_path, 'r') as f:
            self._config = yaml.safe_load(f)
        
        self._substitute_env_vars()
    
    def _substitute_env_vars(self):
        """Replace ${VAR_NAME} patterns with environment variables."""
        def substitute(value):
            if isinstance(value, str):
                pattern = r'\$\{([^}]+)\}'
                matches = re.findall(pattern, value)
                for match in matches:
                    # Support both ${VAR} and ${VAR:-default} forms.
                    if ':-' in match:
                        var_name, default_value = match.split(':-', 1)
                        env_value = os.getenv(var_name, default_value)
                    else:
                        env_value = os.getenv(match, '')
                    value = value.replace(f'${{{match}}}', env_value)
                return value
            elif isinstance(value, dict):
                return {k: substitute(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [substitute(item) for item in value]
            return value
        
        self._config = substitute(self._config)
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-separated key path.
        
        Args:
            key: Configuration key (e.g., 'database.driver')
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        
        return value if value is not None else default
    
    def get_int(self, key: str, default: int = 0) -> int:
        """Get configuration value as integer."""
        value = self.get(key, default)
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get configuration value as boolean."""
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('true', 'yes', '1', 'on')
        return bool(value)
    
    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get configuration value as float."""
        value = self.get(key, default)
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    def get_list(self, key: str, default: Optional[list] = None) -> list:
        """Get configuration value as list."""
        value = self.get(key, default or [])
        if isinstance(value, list):
            return value
        return default or []
    
    def get_dict(self, key: str, default: Optional[dict] = None) -> dict:
        """Get configuration value as dictionary."""
        value = self.get(key, default or {})
        if isinstance(value, dict):
            return value
        return default or {}
    
    def reload(self):
        """Reload configuration from file."""
        self._load_config()
    
    @property
    def config(self) -> Dict[str, Any]:
        """Get full configuration dictionary."""
        return self._config.copy()


_global_config: Optional[ConfigLoader] = None


def get_config(config_path: Optional[str] = None) -> ConfigLoader:
    """
    Get global configuration instance (singleton pattern).
    
    Args:
        config_path: Path to configuration file. Only used on first call.
        
    Returns:
        ConfigLoader instance
    """
    global _global_config
    if _global_config is None:
        _global_config = ConfigLoader(config_path)
    return _global_config
