"""A company's sources and the components that read them (build plan step B7).

A source is read only through the sync component that serves it, registered
for the tenant (builder_component; B13 adds enrolment and monitoring):

  github_runner     reads a GitHub repository with brain-runner's GitHub
                    source: the file listing is cached and brought forward
                    by comparing commits, so a second read fetches only what
                    changed -- the same incremental read a mapping run makes.
  knovas_connector  reads a folder of the file share or DMS through the
                    KnovasConnector's /discover: files with path, size and
                    date, as the connector reports them today.
  http_json         reads a JSON document over HTTPS: its record lists.

Every kind can also run at the company, enrolled under connector protocol v1
(transport ``v1``): a sync component maps records from any of them, and an
enrolled KnovasConnector transfers a folder's documents. The Platform then
reads nothing from a company's network (OnSiteFiles, on_site_problem); whether
the component can read the source it reports as a code and counts.

CRM and database connectors are shown as planned.

A reader answers two questions: ``test`` -- reach the source and list what it
holds as collections with counts -- and ``sample`` -- up to 50 items of one
collection. ``profile`` turns a sample into fields with type, share empty,
distinct values and examples. Nothing here logs: what a source holds (file
names, values) goes to the page that asked, never into a log.
"""
from __future__ import annotations

import posixpath
import re
from collections import Counter
from typing import Any, Callable, Iterable, Mapping, Optional

SAMPLE_LIMIT = 50
COLLECTION_LIMIT = 40
#: Fields a profile shows: the most filled first; a sample of many different files has hundreds.
FIELD_LIMIT = 40
#: The extractors a sample of a repository may use: brain-runner's, the ones that read any file.
FILE_EXTRACTORS = {"yaml_file": "YAML-Dateien", "json_file": "JSON-Dateien",
                   "markdown_front_matter": "Markdown mit Front Matter", "file_glob": "Dateien (Name, Größe, Datum)"}
_EXTRACTOR_OF = {".yaml": "yaml_file", ".yml": "yaml_file", ".json": "json_file", ".md": "markdown_front_matter"}

COMPONENT_KINDS: dict[str, dict] = {
    "github_runner": {"label": "GitHub-Runner", "serves": "github",
                      "text": "Liest GitHub-Repositories mit brain-runner, in der Plattform."},
    "knovas_connector": {"label": "KnovasConnector", "serves": "knovas_connector",
                         "text": "Liest die Dateiablage oder das DMS der Firma, vor Ort.",
                         "settings": [("base_url", "Adresse", "leer: der Connector dieser Plattform")]},
    "http_json": {"label": "HTTP/JSON-Leser", "serves": "http_json",
                  "text": "Liest JSON-Schnittstellen über HTTPS, in der Plattform."},
    "sync_component": {"label": "Sync-Komponente (vor Ort)", "serves": "http_json",
                       "text": "Läuft bei der Firma, liest dort JSON-Schnittstellen, GitHub-Repositories und Dateiablagen "
                               "und schreibt nach Probelauf und Freigabe ins Brain."},
}
PLANNED_COMPONENTS = [
    {"kind": "crm", "label": "CRM-Connector", "text": "Geplant: wartet auf das Connector-Protokoll v1 (SS-534)."},
    {"kind": "database", "label": "Datenbank-Connector", "text": "Geplant: wartet auf das Connector-Protokoll v1 (SS-534)."},
]

SOURCE_KINDS: dict[str, dict] = {
    "github": {"label": "GitHub-Repository", "component": "github_runner",
               "settings": [("repository", "Repository", "owner/name"), ("ref", "Branch oder Tag", "main")],
               "also_on": ["sync_component"],        # on site its token stays in the component's own environment
               "credential": "Lese-Token (nur Contents: read)", "credential_required": True},
    "knovas_connector": {"label": "Dateiablage / DMS", "component": "knovas_connector",
                         "settings": [("folder", "Ordner", "Pfad unter dem Wurzelordner des Connectors"),
                                      ("access_groups", "Zugriffsgruppen",
                                       "für Dokumente: Gruppen mit Komma, oder «alle» für den ganzen Mandanten")],
                         "also_on": ["sync_component"],
                         "credential": None, "credential_required": False},
    "http_json": {"label": "HTTP/JSON", "component": "http_json",
                  "settings": [("url", "Adresse", "https://…")],
                  "also_on": ["sync_component"],     # on site its token stays in the component's own environment
                  "credential": "Token (Bearer), falls nötig", "credential_required": False},
}
PLANNED_SOURCES = [{"kind": "crm", "label": "CRM (Salesforce, HubSpot, …)",
                    "text": "Geplant: wartet auf das Connector-Protokoll v1 (SS-534)."}]

#: What a source allows, per kind of integration. The first is the default. A kind offers ``read_write`` only once a
#: component can really write to it: today none can, so every kind reads. A source is never written unless it says so.
ACCESS = {"read": "Nur lesen", "read_write": "Lesen und schreiben"}


def access_modes(kind: Any) -> list[str]:
    return list(SOURCE_KINDS.get(kind, {}).get("access") or ["read"])


def access_of(source: Mapping[str, Any]) -> str:
    """A source's access; one from before this field reads."""
    return str(source.get("access") or "read")


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class ReaderError(RuntimeError):
    """The source could not be read; the message is for the page and names no content."""


def settings_problem(kind: str, settings: Mapping[str, Any]) -> Optional[str]:
    """Why a source's settings cannot be read, or None."""
    if kind == "github":
        repo, ref = str(settings.get("repository") or ""), str(settings.get("ref") or "")
        if not _REPOSITORY.match(repo):
            return "Repository: in der Form owner/name."
        if ref and (not _REF.match(ref) or ".." in ref or ref.startswith(("/", "-"))):
            return "Branch oder Tag: kein gültiger Name."
    elif kind == "knovas_connector":
        folder = str(settings.get("folder") or "")
        if ".." in folder.replace("\\", "/").split("/"):
            return "Ordner: ohne «..»."
    elif kind == "http_json":
        if not str(settings.get("url") or "").startswith("https://"):
            return "Adresse: eine https://-Adresse."
    return None


# ------------------------------------------------------------------ profile

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][0-9:.]+(Z|[+-]\d{2}:?\d{2})?)?$")


def _flat(value: Any, prefix: str, out: dict, depth: int) -> None:
    if isinstance(value, Mapping) and depth < 4:
        if not value and prefix:
            out[prefix] = {}
        for k, v in value.items():
            _flat(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    else:
        out[prefix or "wert"] = value


def flatten(item: Any) -> dict:
    """An item as fields: nested objects become dotted names, four levels deep."""
    out: dict = {}
    _flat(item, "", out, 0)
    return out


def _type(value: Any) -> Optional[str]:
    if value is None or value == "" or value == [] or value == {}:
        return None
    if isinstance(value, bool):
        return "Wahrheitswert"
    if isinstance(value, (int, float)):
        return "Zahl"
    if isinstance(value, str):
        return "Datum" if _DATE.match(value) else "Text"
    if isinstance(value, list):
        return "Liste"
    if isinstance(value, Mapping):
        return "Objekt"
    return "Text"


def shown(value: Any, limit: int = 120) -> str:
    """A value as the page shows it, shortened."""
    if isinstance(value, list):
        if all(not isinstance(v, (list, Mapping)) for v in value):
            text = ", ".join(str(v) for v in value[:8]) + (" …" if len(value) > 8 else "")
        else:
            text = f"{len(value)} Einträge"
    elif isinstance(value, Mapping):
        text = f"{len(value)} Felder"
    elif value is None:
        text = ""
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def profile(items: Iterable[Any]) -> dict:
    """Fields of a sample: per field its type, share empty, distinct values and examples; and the rows."""
    rows = [flatten(i) for i in items]
    names: list[str] = []
    for row in rows:
        for name in row:
            if name not in names:
                names.append(name)
    fields = []
    for name in names:
        values = [row.get(name) for row in rows]
        types = Counter(t for t in map(_type, values) if t)
        filled = [shown(v) for v in values if _type(v)]
        distinct = list(dict.fromkeys(filled))
        fields.append({
            "name": name,
            "type": "leer" if not types else (types.most_common(1)[0][0] if len(types) == 1 else "gemischt"),
            "types": dict(types),
            "empty_share": round(1 - len(filled) / len(rows), 3) if rows else 0.0,
            "distinct": len(distinct),
            "examples": distinct[:3],
        })
    fields.sort(key=lambda f: f["empty_share"])  # stable: equally filled fields keep their order
    kept = [f["name"] for f in fields[:FIELD_LIMIT]]
    return {"count": len(rows), "fields": fields[:FIELD_LIMIT], "more_fields": max(0, len(fields) - FIELD_LIMIT),
            "rows": [{name: shown(row.get(name)) for name in kept} for row in rows]}


# ------------------------------------------------------------------ readers

def github_selection(collection: Any) -> tuple[str, str]:
    """(reader, file pattern) of a selection in a repository, from the list of a test or typed by a person: a path
    copied from Windows counts, a pattern that leaves the repository does not."""
    extractor, _, pattern = str(collection).strip().partition(":")
    pattern = pattern.strip().replace("\\", "/")
    if extractor not in FILE_EXTRACTORS or not pattern or pattern.startswith("/") or ".." in pattern.split("/"):
        raise ReaderError("Diese Auswahl liest der GitHub-Runner nicht.")
    return extractor, pattern


#: What GitHub's answers mean to the person at the page. A wrong branch is a 422, not a 404: GitHub's commits call says so.
_GITHUB_ANSWERS = {
    401: "GitHub weist das Token ab (401): es fehlt, ist falsch oder abgelaufen. Das Token neu setzen.",
    403: "GitHub verweigert den Zugriff (403): dem Token das Recht «Contents: read» für dieses Repository geben. "
         "Eine Organisation muss das Token unter Umständen zuerst freigeben.",
    404: "GitHub findet das Repository nicht (404): die Schreibweise owner/name prüfen. Ein privates Repository sieht "
         "GitHub auch dann nicht, wenn das Token es nicht lesen darf.",
    422: "Diesen Branch oder Tag gibt es im Repository nicht (422): den Namen prüfen, oft «main» oder «master». "
         "Ein Ordner gehört nicht hierher; er wird beim Zuordnen gewählt.",
}


def _github_failure(exc: Exception) -> Optional[str]:
    """What to fix, from brain-runner's SourceError, which carries its cause only as text. None when it says nothing
    known. The sentences are fixed: nothing of the error's text reaches the page."""
    text = str(exc)
    answered = re.search(r"answered (\d{3})", text)
    if answered:
        status = int(answered.group(1))
        return _GITHUB_ANSWERS.get(status, f"GitHub antwortet mit einem Fehler ({status}): später noch einmal versuchen.")
    if "rate limit" in text:
        return "GitHub begrenzt die Anfragen gerade: später noch einmal versuchen."
    if "did not resolve the ref" in text:
        return _GITHUB_ANSWERS[422]
    if re.search(r"failed: \w+", text) or "no answer after" in text:
        return "Die Plattform erreicht GitHub nicht: Netzwerk oder Firewall des Servers prüfen."
    return None


class GitHubReader:
    """A repository through brain-runner's source (GitHubSource; a LocalSource reads a checkout the same way)."""

    def __init__(self, source):
        self.src = source

    def _files(self) -> list[str]:
        try:
            return self.src.glob("**/*")
        except Exception as exc:  # noqa: BLE001 - brain-runner's SourceError and transport errors alike
            raise ReaderError(_github_failure(exc) or f"Das Repository ist nicht lesbar ({type(exc).__name__}).") from None

    def test(self) -> dict:
        files = self._files()
        groups: Counter = Counter()
        for path in files:
            parts = posixpath.dirname(path).split("/") if posixpath.dirname(path) else []
            ext = posixpath.splitext(path)[1].lower()
            groups[("/".join(parts[:2]), len(parts) >= 2, ext)] += 1
        collections = []
        for (folder, deep, ext), count in groups.most_common():
            pattern = (f"{folder}/**/*{ext}" if deep else (f"{folder}/*{ext}" if folder else f"*{ext}")) if ext else None
            if pattern is None:
                continue  # files without an extension: listed in the counts, not as a collection
            extractor = _EXTRACTOR_OF.get(ext, "file_glob")
            collections.append({"id": f"{extractor}:{pattern}", "label": f"{folder or '(Wurzel)'} · *{ext}",
                                "kind": "Dateien", "count": count, "extractor": extractor})
        detail = {}
        for name in ("head", "listing", "downloaded", "reused"):
            value = getattr(self.src, name, None)
            if value not in (None, ""):
                detail[name] = value[:12] if name == "head" else value
        extensions = Counter(posixpath.splitext(p)[1].lower() or "(ohne)" for p in files)
        return {"collections": collections[:COLLECTION_LIMIT], "more": max(0, len(collections) - COLLECTION_LIMIT),
                "counts": [["Dateien", len(files)], ["Ordner", len({posixpath.dirname(p) for p in files})],
                           ["Dateiarten", len(extensions)]],
                "detail": detail}

    def sample(self, collection: str, limit: int = SAMPLE_LIMIT) -> list[dict]:
        extractor, pattern = github_selection(collection)
        from brain_runner.extractors import extract

        out = []
        try:
            for doc in extract(self.src, {"extractor": extractor, "glob": pattern}):
                doc = dict(doc)
                doc.pop("last_commit", None)
                out.append(doc)
                if len(out) >= limit:
                    break
        except Exception as exc:  # noqa: BLE001
            raise ReaderError(_github_failure(exc) or f"Die Auswahl ist nicht lesbar ({type(exc).__name__}).") from None
        return out


    def documents(self, spec: Mapping[str, Any]):
        """The documents a mapping's source names, read by brain-runner's extractor (step B8)."""
        from brain_runner.extractors import extract

        return extract(self.src, dict(spec))


class KnovasConnectorReader:
    """A folder of the file share or DMS, through the KnovasConnector's /discover."""

    DEPTH = 4

    def __init__(self, client, folder: str = ""):
        self.client = client
        self.folder = folder.strip()

    def _entries(self) -> tuple[list[dict], bool]:
        try:
            found = self.client.discover(root=self.folder or None, max_depth=self.DEPTH)
        except PermissionError as exc:
            raise ReaderError(str(exc) or "Kein angemeldeter Benutzer.") from None
        except Exception as exc:  # noqa: BLE001 - KnovasConnectorError carries a message without content
            raise ReaderError(f"Der Connector antwortet nicht: {exc}") from None
        entries = [e for e in found.get("entries") or [] if isinstance(e, Mapping)]
        return [e for e in entries if e.get("type") == "file"], bool(found.get("truncated"))

    @staticmethod
    def _sub(path: str) -> str:
        path = str(path).replace("\\", "/").strip("/")
        return path.split("/", 1)[0] if "/" in path else ""

    def test(self) -> dict:
        files, truncated = self._entries()
        subs = Counter(self._sub(f.get("path", "")) for f in files)
        collections = [{"id": "*", "label": "Alle Dateien", "kind": "Dateien", "count": len(files)}]
        collections += [{"id": f"folder:{name}", "label": name or "(direkt im Ordner)", "kind": "Dateien", "count": n}
                        for name, n in sorted(subs.items(), key=lambda kv: (-kv[1], kv[0]))][:COLLECTION_LIMIT]
        extensions = Counter(posixpath.splitext(str(f.get("name") or ""))[1].lower() or "(ohne)" for f in files)
        counts = [["Dateien", len(files)], ["Ordner", len(subs)], ["Dateiarten", len(extensions)]]
        return {"collections": collections, "more": 0, "counts": counts,
                "detail": {"extensions": dict(extensions.most_common(8)), **({"truncated": True} if truncated else {})}}

    def sample(self, collection: str, limit: int = SAMPLE_LIMIT) -> list[dict]:
        files, _ = self._entries()
        if collection != "*":
            kind, _, name = str(collection).partition(":")
            if kind != "folder":
                raise ReaderError("Diese Auswahl gibt es im Connector nicht.")
            files = [f for f in files if self._sub(f.get("path", "")) == name]
        out = []
        for f in files[:limit]:
            path = str(f.get("path") or "").replace("\\", "/")
            out.append({"path": path, "name": f.get("name"), "folder": posixpath.dirname(path),
                        "extension": posixpath.splitext(str(f.get("name") or ""))[1].lower(),
                        "size_bytes": f.get("size_bytes"), "modified_at": f.get("modified_at")})
        return out


    def documents(self, spec: Mapping[str, Any]) -> list[dict]:
        """The files a mapping's source names (its glob under the connector's root), one document each, shaped as
        brain-runner's file_glob gives them on the folder itself (step B8)."""
        files, _ = self._entries()
        base = str(spec.get("glob") or "**/*").removesuffix("**/*").rstrip("/")
        folder = self.folder.strip("/")
        out = []
        for f in files:
            rel = str(f.get("path") or "").replace("\\", "/").strip("/")
            full = f"{folder}/{rel}" if folder else rel
            if base and not full.startswith(base + "/"):
                continue
            name = str(f.get("name") or posixpath.basename(full))
            stem, suffix = posixpath.splitext(name)
            out.append({"path": full, "name": name, "stem": stem, "suffix": suffix, "size": f.get("size_bytes"),
                        "modified": f.get("modified_at"),
                        "folders": [x for x in posixpath.dirname(full).split("/") if x]})
        return out


class OnSiteFiles:
    """A file share read by a component at the company: the Platform sees no file of it. A file's fields are always
    the same, so a mapping is designed on one made-up example; how many files there are comes from the component's
    report. A plan against the Brain runs on the component, never on this example (builder_routes.mapping_dry_run)."""

    EXAMPLE = {"path": "Beispiel/Unterordner/Dokument.pdf", "name": "Dokument.pdf", "stem": "Dokument", "suffix": ".pdf",
               "size": 1024, "modified": "2026-01-31", "folders": ["Beispiel", "Unterordner"]}

    def __init__(self, check: Optional[Mapping[str, Any]] = None):
        self.check = check or {}

    def test(self) -> dict:
        files = (self.check.get("counts") or {}).get("files")
        return {"collections": [{"id": "*", "label": "Alle Dateien (Beispiel: gelesen wird bei der Firma)", "kind": "Dateien",
                                 "count": files or 0}], "more": 0,
                "counts": [["Dateien", files]] if files is not None else [], "detail": {"on_site": True}}

    def sample(self, collection: str, limit: int = SAMPLE_LIMIT) -> list[dict]:
        return [dict(self.EXAMPLE)]

    def documents(self, spec: Mapping[str, Any]) -> list[dict]:
        return [dict(self.EXAMPLE)]


def on_site_problem(kind: Any, secret: Optional[str]) -> Optional[str]:
    """Why the Platform cannot read this source that a component reads at the company, or None."""
    if kind == "github" and not secret:
        return ("Diese Quelle liest die Komponente bei der Firma, mit ihrem eigenen Token. Um hier Felder zu sehen und die "
                "Zuordnung zu entwerfen, hier zusätzlich ein Lese-Token setzen. Ob die Komponente die Quelle erreicht, "
                "zeigt ihre Seite unter «Verbindung prüfen».")
    return None


def public_https(url: str, resolve: Optional[Callable[[str], list]] = None) -> str:
    """Refuse an address the Platform must not call for a source: not https, or a host that resolves to a
    loopback, private, link-local or otherwise non-public address (every address it resolves to)."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ReaderError("Die Adresse muss mit https:// beginnen.")
    try:
        infos = (resolve or (lambda host: socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)))(parts.hostname)
    except OSError:
        raise ReaderError("Der Name der Adresse ist nicht auflösbar.") from None
    addresses = sorted({info[4][0] for info in infos})
    if not addresses or any(not ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses):
        raise ReaderError("Die Adresse zeigt nicht ins öffentliche Netz; solche Quellen liest ein Connector vor Ort.")
    return addresses[0]


def _pinned_get(url: str, address: str, **kwargs):
    """GET ``url`` from ``address`` (checked by public_https), never from a second lookup of its name: the
    connection goes to that address, TLS checks the certificate against the name, the Host header carries it."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    host = parts.hostname

    class Pinned(HTTPAdapter):
        def init_poolmanager(self, connections, maxsize, block=False, **pool):
            pool.update(server_hostname=host, assert_hostname=host)
            super().init_poolmanager(connections, maxsize, block, **pool)

    literal = f"[{address}]" if ":" in address else address
    target = urlunsplit(("https", f"{literal}:{parts.port}" if parts.port else literal, parts.path or "/", parts.query, ""))
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Host"] = parts.netloc.rsplit("@", 1)[-1]
    with requests.Session() as session:
        session.mount("https://", Pinned())
        return session.get(target, headers=headers, **kwargs)


class HttpJsonReader:
    """A JSON document over HTTPS: its lists of records are the collections.

    Only public addresses are called, every resolved address checked, and redirects are not followed: a
    source's address is not a way into the Platform's own network.
    """

    MAX_BYTES = 5_000_000

    def __init__(self, url: str, token: Optional[str] = None, get: Optional[Callable[..., Any]] = None,
                 resolve: Optional[Callable[[str], list]] = None):
        self.url = url
        self.token = token
        self._get = get
        self._resolve = resolve
        self._data: Any = None

    def _document(self) -> Any:
        if self._data is not None:
            return self._data
        address = public_https(self.url, self._resolve)
        get = self._get
        if get is None:
            get = lambda url, **kw: _pinned_get(url, address, **kw)  # noqa: E731
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = get(self.url, headers=headers, timeout=20, stream=True, allow_redirects=False)
        except Exception as exc:  # noqa: BLE001
            raise ReaderError(f"Die Adresse ist nicht erreichbar ({type(exc).__name__}).") from None
        if 300 <= resp.status_code < 400:
            raise ReaderError("Die Adresse leitet weiter; Weiterleitungen werden nicht verfolgt.")
        if resp.status_code >= 400:
            raise ReaderError(f"Die Adresse antwortet mit HTTP {resp.status_code}.")
        body = resp.content
        if len(body) > self.MAX_BYTES:
            raise ReaderError("Die Antwort ist größer als 5 MB.")
        try:
            import json

            self._data = json.loads(body)
        except ValueError:
            raise ReaderError("Die Antwort ist kein JSON.") from None
        return self._data

    def _lists(self) -> dict[str, list]:
        data = self._document()
        found: dict[str, list] = {}
        if isinstance(data, list):
            found["$"] = data
        elif isinstance(data, Mapping):
            for key, value in data.items():
                if isinstance(value, list):
                    found[f"$.{key}"] = value
                elif isinstance(value, Mapping):
                    for inner, items in value.items():
                        if isinstance(items, list):
                            found[f"$.{key}.{inner}"] = items
        return found

    def test(self) -> dict:
        lists = self._lists()
        collections = [{"id": path, "label": path, "kind": "Datensätze", "count": len(items)}
                       for path, items in lists.items()]
        return {"collections": collections, "more": 0,
                "counts": [["Listen", len(lists)], ["Datensätze", sum(len(v) for v in lists.values())]], "detail": {}}

    def documents(self, spec: Mapping[str, Any]) -> list[Any]:
        """The response as the one document brain-runner's json_file gives: ``{path, data}`` (step B8)."""
        return [{"path": str(spec.get("path") or "response.json"), "data": self._document()}]

    def sample(self, collection: str, limit: int = SAMPLE_LIMIT) -> list[Any]:
        lists = self._lists()
        if collection not in lists:
            raise ReaderError("Diese Liste gibt es in der Antwort nicht.")
        return lists[collection][:limit]
