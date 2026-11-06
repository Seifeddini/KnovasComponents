"""Where the tenant's knowledge base keeps the files a change request writes besides the plan (V1-G4).

A change set edits a feature plan and, depending on the change, three more files of the tenant's repository:

* ``decisions_dir``: the folder a substantive change adds its decision record to (``DEC-nnnn-….md``);
* ``plan_links_path``: the YAML edge list holding a plan's capability, goal and hypothesis links;
* ``sample_edges_path``: the YAML edge list holding pre-mortems and risk mitigations of a plan.

These are the tenant's layout, not the product's, so the product names none of them: each is empty until the
deployment sets it (``views.change_requests.*`` in config.yaml, or the ``VIEWS_KB_*`` variable). Empty means
"not configured". The feature that needs a path says so and writes nothing; it never guesses a default.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Dict

#: Field -> (config key, environment variable): what the "not configured" message names.
SETTINGS: Dict[str, tuple] = {
    "decisions_dir": ("views.change_requests.decisions_dir", "VIEWS_KB_DECISIONS_DIR"),
    "plan_links_path": ("views.change_requests.plan_links_path", "VIEWS_KB_PLAN_LINKS_PATH"),
    "sample_edges_path": ("views.change_requests.sample_edges_path", "VIEWS_KB_SAMPLE_EDGES_PATH"),
}


def _norm(value: Any) -> str:
    """A repository path as the repository clients take it: forward slashes, no leading or trailing slash."""
    text = str(value or "").strip().replace("\\", "/").strip("/")
    if ".." in text.split("/"):
        raise ValueError(f"{value!r} ist kein Pfad im Repository (relativ, ohne ..).")
    return text


@dataclass(frozen=True)
class RepoLayout:
    decisions_dir: str = ""
    plan_links_path: str = ""
    sample_edges_path: str = ""

    def __post_init__(self) -> None:
        for f in dataclasses.fields(self):
            object.__setattr__(self, f.name, _norm(getattr(self, f.name)))

    @classmethod
    def from_config(cls, config: Any) -> "RepoLayout":
        return cls(**{name: config.get(key, "") or "" for name, (key, _) in SETTINGS.items()})

    def paths(self) -> Dict[str, str]:
        """The configured file paths (not the folder), by field."""
        return {name: value for name, value in (("plan_links_path", self.plan_links_path),
                                                ("sample_edges_path", self.sample_edges_path)) if value}


_layout = RepoLayout()


def configure(layout: RepoLayout) -> RepoLayout:
    """Set the installation's layout (app start); returns the previous one."""
    global _layout
    previous, _layout = _layout, layout
    return previous


def current() -> RepoLayout:
    return _layout


def names_file(source: str, path: str) -> bool:
    """Whether an edit-map source names this repository path: the path itself, or a tail of it."""
    source = str(source or "").strip().strip("/")
    return bool(source) and bool(path) and (path == source or path.endswith("/" + source))


def not_configured(field: str) -> str:
    """The message a feature shows when the path it needs is empty."""
    key, env = SETTINGS[field]
    return f"Nicht konfiguriert: {key} ({env}) ist leer; die Plattform rät keinen Pfad im Repository."
