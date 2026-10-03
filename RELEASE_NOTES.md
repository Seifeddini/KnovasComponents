# Unreleased

## Dokumentfelder: Hinweise mit Feld, Stichwoerter und Status, erneutes Senden

- **Hinweise von Knovas nennen das Feld.** Der Reiter *Ingestion* zeigt die
  Upload-Hinweise des letzten Durchlaufs je Code und Feldschluessel, z.B.
  `invalid_value 3x (amount)`, und darunter einmal die Bedeutung jedes
  Codes; nie einen Wert. Ein aelterer Knovas Connector liefert nur Codes.
- **Nicht uebernommene Felder ohne Anlass erneut senden.** Hat Knovas Felder
  nicht angenommen, fragt der Knovas Connector hoechstens einmal pro Stunde
  nach (`GET /secured/graph/doc-fields`). Nimmt Knovas sie an, sendet er
  diese Dokumente erneut, hoechstens 100 pro Durchlauf -- auch wenn sonst
  nichts hochgeladen wird.
- **Stichwoerter und Status aus Datei-Eigenschaften.** Zwei neue Opt-ins pro
  Ordner: *Stichwoerter aus Datei-Eigenschaften (PDF/Word-Stichwoerter,
  Outlook-Kategorien)* fuellt `keywords`, *Status aus Word-Dokumentstatus*
  fuellt `status`; Knovas uebernimmt einen Status nur, wenn er zu einer
  Status-Auswahl passt. Einschalten sendet die Dokumente des Ordners erneut
  (verrechnet, mit Bestaetigung) und braucht einen Knovas Connector, der
  `metadata_fields_v2` meldet. Outlook-Kategorien liest knovas-extract
  0.4.0a1 noch nicht.
- **E-Mail-Datum als Dokumentdatum funktioniert.** Gesendet wird der Tag
  aus dem `Date`-Header (`2024-03-15`). Bisher ging der Zeitstempel mit
  Uhrzeit hinaus, den Knovas als `invalid_value` ablehnt -- keine E-Mail
  erhielt ein Dokumentdatum. E-Mails in Ordnern mit dieser Option werden
  einmal erneut gesendet (verrechnet, hoechstens 100 je Durchlauf), andere
  Dokumente nicht.

## Neu extrahieren nach einem Extraktor-Update

Neuere Versionen von knovas-extract lesen manche Dokumente besser (Tabellen
in Word-Dateien, Seitenzahlen grosser Scans, Texterkennung je Seite). Bereits
indexierte Dokumente profitieren davon erst, wenn sie neu extrahiert werden.

- *Verwaltung -> Ingestion* zeigt "N Dokumente mit aelterer Extraktion" und
  bietet Administratoren *Neu extrahieren* an. Ein Dialog nennt vorher
  Anzahl, Kosten und Dauer wie bei einer Feldaenderung; ohne diese
  Bestaetigung wird nichts neu extrahiert. Die Anfrage steht mit Zahlen im
  Protokoll (`ingestion.reextract_requeued`).
- Der Knovas Connector liest die Dokumente neu, hoechstens
  `RC_REEXTRACT_PER_CYCLE` (100) je Durchlauf, nach neuen, geaenderten und
  wegen Feldern erneut zu sendenden Dateien -- teilweise extrahierte zuerst,
  dann PDF, Word, E-Mails. Gesendet wird nur, was sich geaendert hat (Text,
  Seitenzahlen, Felder, Titel, Beschreibung); jedes gesendete Dokument ist
  ein verrechneter Upload. Beim ersten Mal nach diesem Update werden alle
  gesendet, weil der Vergleichswert noch fehlt.
- `GET /sync/status` des Connectors meldet `extraction.outdated`, `queued`
  und `per_cycle` -- nur Zahlen.

## Extraktor: knovas-extract 0.4.0a1, eine Version fuer beide Seiten

- **Plattform und Knovas Connector installieren dieselbe, fest gepinnte
  Version von knovas-extract** (`ARG KNOVAS_EXTRACT_VERSION` /
  `KNOVAS_EXTRACT_GIT_REF` in beiden Dockerfiles). Bisher kam der Extraktor
  aus dem beweglichen `main` des Bibliotheks-Repositorys, und ein Server mit
  Docker-Cache behielt einen alten Stand. Der naechste `./scripts/start.sh`
  baut beide Images neu.
- **Outlook-Mails, deren Text nur als RTF vorliegt**, werden gelesen (bisher
  leerer Text).
- *Verwaltung -> System* nennt die Extraktor-Version der Plattform und des
  Knovas Connector und warnt, wenn sie sich unterscheiden oder der Knovas
  Connector keine meldet (dann ist er aelter als die Plattform).
- **selectolax bleibt unter Version 1.0.** selectolax 1.0.0 (3.10.2026) hat
  das Modul entfernt, mit dem knovas-extract 0.4.0a1 HTML liest; ein neu
  gebautes Image haette die Vorschau von Word-Dateien und E-Mails
  gebrochen. Beide Seiten begrenzen die Version, und CI liest in beiden
  Images eine HTML-Seite.

## Dokumentfelder (Dokumentwerte)

Typisierte Werte je Dokument -- Mandant, Zeitraum, Dokumentart, Gericht,
Frist -- als Filter in der Suche, als Liste, auf den Trefferkarten und im
Feldbereich der Vorschau.

- **Bei Knovas fuer jeden Mandanten eingeschaltet.** Seit Knovas 1.5.0 sind
  Dokumentfelder, auch das Filtern nach Feldern in Suche und Listen, fuer
  jeden Mandanten eingeschaltet; nichts in `knovas.env` schaltet sie ein.
  Knovas kann sie fuer einen Mandanten abschalten, und aeltere Server kennen
  sie nicht: Plattform und Knovas Connector pruefen jede Antwort und bleiben,
  solange die Funktion aus ist, wie bisher, ohne neue Oberflaeche und ohne
  neue Schluessel in den Anfragen. *Verwaltung -> System -> Dokumentfelder*
  nennt die Stufe: `aus`, `Werte (ohne Filter)`, `Werte + Liste (Filter in
  der Suche: Kalibrierung bei Knovas fehlt)` oder `Werte + Filter`.
  `./scripts/doctor.sh` prueft dasselbe.
- **BROKERED-Mandanten:** Entitaetswerte vom Knovas Connector (z.B. `client`)
  brauchen zusaetzlich Aenderung S1; vorher lehnt Knovas solche Uploads mit
  `assertion_rejected` ab, und der Knovas Connector indexiert das Dokument
  ohne Felder.
- **Was die Plattform zeigt**, je nach Stufe: den Feldbereich in der Vorschau
  (Werte, Herkunft *Manuell / Upload / Ordnervorgabe*, Hinweise) und den
  Reiter *Dokumentfelder* (Felder, Pakete `core` und `legal_ch`,
  Einstellungen, Ordnervorgaben); dann Werte auf den Trefferkarten, *Liste
  anzeigen* und den *Feldfilter* unter *Dokumente*; zuletzt die Filterleiste
  der Suche. Bearbeiten duerfen die Rollen in `DOC_FIELDS_EDIT_ROLES`
  (Standard `admin`); besonders schuetzenswerte Felder nur `admin`.
  `DOC_FIELDS_UI=off` blendet alles aus.
- **Filter gelten nur, wenn Knovas sie bestaetigt.** Ohne Bestaetigung zeigt
  die Suche keine Treffer und bietet *Ohne Filter suchen* an -- nie still
  ungefilterte Treffer. Listen sagen "Liste unvollstaendig", wenn sie es sind;
  eine Liste nach Frist ist ausdruecklich keine Fristenkontrolle. Ein
  bearbeiteter Titel wird angezeigt, nicht durchsucht.
- **Was der Knovas Connector sendet:** je Ordner der Ingestion feste Werte
  (`schluessel = Wert; Wert2`), Pfadvorlagen (`{mandant}/{period}/**`) und
  gewaehlte Dateieigenschaften, als Upload-Werte bei jedem Upload. Felder
  blockieren nie die Indexierung: lehnt Knovas sie ab, wird ohne Felder
  indexiert, und eine Pfadvorlage, die der Knovas Connector nicht uebersetzen
  kann, lehnt er schon beim Speichern ab. `RC_DOC_FIELDS=off` schaltet das
  Senden ab.
- **Was erneutes Senden kostet:** Aendern sich die Felder eines Ordners,
  sendet der Knovas Connector alle seine Dokumente erneut -- jedes ein
  verrechneter Upload mit erneuter Texterkennung --, hoechstens
  `RC_FIELDS_REUPLOAD_PER_CYCLE` (100) je Durchlauf und erst nach neuen und
  geaenderten Dateien. 20'000 Dokumente brauchen beim naechtlichen Zeitplan ca.
  3 Naechte (ohne Texterkennungszeit gerechnet), beim Zeitplan *manuell* 200
  Starts. Die Ingestion zeigt Anzahl und Dauer vor dem Speichern und verlangt
  eine Bestaetigung. Werte, die nicht vom Ordner abhaengen, gehoeren in eine
  Ordnervorgabe: sie gelten ohne erneutes Senden.
- **Zugriffsprotokolle ohne Adressen.** Das mitgelieferte nginx und gunicorn
  protokollieren nur Zeit, Methode, Status, Groesse und Dauer
  (`knovas_privacy`), nicht mehr die aufgerufene Adresse: Plattform-Adressen
  enthalten Dokumentpfade, und diese nennen Mandanten. Die Vorlage fuer das
  Host-nginx tut dasselbe -- bestehende Installationen kopieren
  `knovas-login-limit.conf` erneut nach `/etc/nginx/conf.d/` (dort steht jetzt
  auch das Protokollformat) und erneuern die Seite aus der Vorlage
  (`./scripts/host-https.sh` erledigt beides). Bekannte Grenze: Vorschau und
  Oeffnen tragen den Dokumentpfad und die Suchwoerter weiterhin in der Adresse;
  ein eigener Proxy davor muss ebenso ohne Adressen protokollieren, und das
  nginx-Fehlerprotokoll nennt die Adresse, wenn eine Anfrage an die Plattform
  scheitert.
- Der neue Code fuer Dokumentfelder schreibt keine Feldwerte, Titel, Pfade
  oder Suchtexte in Logzeilen, Audit-Eintraege oder neue Adressen. Bekannte
  Grenze: aeltere Logzeilen von Plattform und Knovas Connector nennen
  weiterhin Dokumentpfade und Verweise (Oeffnen, Herunterladen und Vorschau
  eines Dokuments, fehlgeschlagene Vorschau, die alte Suche mit ihrem
  Suchtext, fehlgeschlagene oder teilweise Uploads, entfernte Dokumente).
  Ordnernamen sind die Quelle von Pfadvorlagen-Werten, also koennen diese
  Zeilen auch Feldwerte enthalten: Container-Logs vertraulich behandeln wie die
  Dokumente.
- Umbenennen oder Verschieben einer Datei macht sie bei Knovas zu einem neuen
  Dokument; manuelle Werte des alten werden nicht uebernommen. Ein Downgrade
  der Plattform verwirft die Felder je Ordner; uebertraegt die alte Plattform
  das Profil, loescht der Knovas Connector die Upload-Werte der betroffenen
  Dokumente beim naechsten erneuten Senden.

Anleitung: [KnovasPlatform/docs/features/document-fields.md](KnovasPlatform/docs/features/document-fields.md),
fuer Kunden: [docs/client/document-fields.md](docs/client/document-fields.md),
Knovas Connector: [RemoteController/CHANGELOG.md](RemoteController/CHANGELOG.md) (0.3.0).

Die API-Referenz `docs/KnovasAPI/Secure_API.md` ist zugunsten des Knovas
Developer Kit stillgelegt (wie zuvor `KnovasPlatform/knovas-docs/`); die
Dokumentfelder stehen nur dort.

## nginx wartet so lange wie gunicorn

Das mitgelieferte nginx (`docbridge-web-nginx`) und die Host-nginx-Vorlage
warten jetzt 180 s auf die Plattform (`proxy_read_timeout`), so lange wie
gunicorn. Mit 120 s gab nginx genau dann auf, wenn die Textextraktion eines
Admin-Uploads an ihrer 120-s-Grenze abbrach, und statt der Meldung kam ein
504. Bestehende Installationen erstellen nach dem Update das mitgelieferte
nginx neu:
`docker compose --env-file knovas.env up -d --force-recreate docbridge-web-nginx`.
`./scripts/start.sh` allein tut das nicht, und nginx liest seine
Konfiguration nur beim Start: ohne diesen Schritt wartet es weiter nur 120 s
und schreibt weiter die aufgerufenen Adressen ins Zugriffsprotokoll (siehe
*Zugriffsprotokolle ohne Adressen* unter Dokumentfelder). Ausserdem erneuern
sie die Host-nginx-Seite aus der Vorlage
(`./scripts/host-https.sh` erledigt das). Das Image der Plattform startet
gunicorn wie compose (`DOCBRIDGE_WEB_TIMEOUT`, Zugriffsprotokoll ohne
Adressen), auch wenn es ohne compose laeuft.

## RemoteController heisst jetzt Knovas Connector

Nur der Name, den man liest: Dokumentation, Oberflaeche und Ausgaben der
Skripte. Ordner `RemoteController/`, Docker-Dienst `remote-controller`, die
`RC_*`-Einstellungen und die Konfigurationsschluessel bleiben, wie sie sind --
eine bestehende Installation wird ohne Aenderung an `knovas.env` aktualisiert.

## Dokumente in OneDrive und SharePoint (`KNOVAS_DOCUMENTS_URL`)

Statt `KNOVAS_DOCUMENTS_PATH` kann `knovas.env` die Adresse eines OneDrive- oder
SharePoint-Ordners nennen, so wie der Browser sie zeigt, dazu `M365_CLIENT_ID`
und `M365_CLIENT_SECRET` einer Entra-App mit der Anwendungsberechtigung
`Sites.Read.All`. Eine Einstellung fuer beide.

- **Keine Kopie auf dem Server.** Knovas Connector fragt Microsoft Graph nach
  Aenderungen, laedt nur neue und geaenderte Dateien in ein temporaeres
  Verzeichnis, indexiert sie und loescht sie wieder.
- **Oeffnen und Vorschau in Microsoft 365.** Treffer oeffnen in
  OneDrive/SharePoint; die Vorschau ist der Viewer von Microsoft 365, auf der
  Seite der Fundstelle. Textauszuege und Fundstellen bleiben wie bisher.
- **Uebernahme im Admin-Bereich** zeigt die Unterordner aus OneDrive/SharePoint;
  Zugriffsgruppen je Ordner funktionieren unveraendert.
- Das Client-Secret gelangt nur in den Knovas-Connector-Container, nie in die
  generierten `.env.generated` und nie in die Plattform.
- `start.sh` und `doctor.sh` pruefen Anmeldung, Adresse und Ordner.

Anleitung: [docs/microsoft-365.md](docs/microsoft-365.md).

## Eigener Azure-Server je Kunde

- `scripts/azure/create-server.sh` legt VM, Firewall, Entra-Anmeldung und
  taegliche Sicherung in einem Schritt an; `scripts/azure/cloud-init.yaml`
  installiert Docker, nginx, certbot und automatische Sicherheitsupdates.
- `scripts/host-https.sh <name> <email>` holt das Let's-Encrypt-Zertifikat und
  richtet die nginx-Seite ein.
- Die nginx-Vorlage setzt `X-Forwarded-For` statt es zu verlaengern (sonst
  konnte ein Browser die protokollierte IP selbst waehlen), drosselt `/login`
  je Adresse und verschweigt die nginx-Version. Bestehende Installationen:
  `knovas-login-limit.conf` nach `/etc/nginx/conf.d/` kopieren und die
  Seite aus der Vorlage erneuern.

Schritt fuer Schritt: [docs/azure-server.md](docs/azure-server.md).

Behoben dabei: Das Standardprofil von Knovas Connector uebernahm keine Dateien,
die direkt im obersten Ordner liegen (`**/*.pdf` braucht vor Python 3.13 ein
Unterverzeichnis).

# v1.0.0

Customer deploy bundle for Knovas.

## KnovasPlatform

Docker search UI for an indexed Knovas tenant. Requires mTLS client certificates
and a first-administrator address (`PLATFORM_ADMIN_EMAIL`).

### Breaking: persoenliche Konten statt gemeinsamem Firmenpasswort

Die Anmeldung erfolgt jetzt mit **persoenlichen Konten**; das gemeinsame
Firmenpasswort ist abgeloest. `IDENTITY_ENABLED` ist standardmaessig `true`.

**Eine bestehende Installation startet nach dem Upgrade nicht mehr**, solange
`COMPANY_LOGIN_NAME` / `COMPANY_LOGIN_PASSWORD` gesetzt sind. Das ist
beabsichtigt: waeren beide Wege gleichzeitig offen, bliebe genau die
Konfiguration bestehen, die dieses Release beseitigt. Der Start bricht mit
einer Meldung ab, die den naechsten Schritt nennt.

Migration:

1. `PLATFORM_ADMIN_EMAIL` in `knovas.env` setzen.
2. `COMPANY_LOGIN_NAME` und `COMPANY_LOGIN_PASSWORD` aus `knovas.env` entfernen.
3. `./scripts/setup.sh` ausfuehren — legt `secrets/platform_db_password` (0600)
   an und mountet es als Docker-Secret.
4. `./scripts/start.sh`. Beim ersten Start entsteht das Administratorkonto; das
   Einmalpasswort steht in `/app/data/platform-admin-bootstrap` im Container
   `docbridge-web` -- auf einem benannten Volume, es ueberlebt also ein
   Neuerstellen des Containers. Danach anmelden, Passwort aendern, Datei
   loeschen. Wer das Passwort lieber selbst setzt, traegt
   `PLATFORM_ADMIN_PASSWORD` in `knovas.env` ein; dann wird nichts auf Platte
   geschrieben. `./scripts/admin-password.sh` setzt es jederzeit neu und hebt
   dabei eine Sperre auf.
5. **Die Identitaetsdatenbank sichern.** Sie haelt alle Konten, Rollen und
   Gruppenzuordnungen der Kanzlei. Ohne Backup sind sie verloren.

Wer die Umstellung staffeln will, setzt `IDENTITY_ENABLED=false` **und** behaelt
`COMPANY_LOGIN_NAME` / `COMPANY_LOGIN_PASSWORD` — als bewusste Entscheidung,
nicht als Vorgabe. `setup.sh` weist beide Haelften dieser Wahl zurueck, wenn sie
sich widersprechen, statt den Fehler erst im Container auftauchen zu lassen.

### Ein Stack statt zwei

Der eigene Compose-Stack unter `KnovasPlatform/` ist entfernt:
`docker-compose.yml`, `docker-compose.host-nginx.yml`, `start_stack.sh` /
`.ps1`, `stop_stack.sh` / `.ps1`, `scripts/start_stack_host_nginx.sh` und
`.env.example`. Er kannte weder `platform-db` noch `PLATFORM_ADMIN_EMAIL` und
konnte diese Version daher nicht starten — der Container lief los und wurde
`unhealthy`.

Alles laeuft ab sofort aus dem Repository-Wurzelverzeichnis:

```bash
cd KnovasComponents
cp knovas.env.example knovas.env   # ausfuellen
./scripts/setup.sh && ./scripts/start.sh
```

- Der Stack bindet ausschliesslich `127.0.0.1` — die Overlay-Datei fuer den
  Host-NGINX-Betrieb entfaellt, weil das jetzt die Vorgabe ist.
- Zertifikate liegen im **Wurzelverzeichnis** `certs/`, unter den Namen, die
  Knovas ausliefert. `setup.sh` legt die vom Platform erwarteten Namen als
  Symlinks daneben; nichts muss mehr von Hand umbenannt werden.
- Die Demo-API (`knovas-mock`) ist in den Wurzel-Stack uebernommen und bleibt
  profilgebunden: `docker compose --env-file knovas.env --profile mock up -d`.

Laufen noch Container aus dem alten Stack, vorher `docker rm -f docbridge-web
docbridge-web-nginx` — sonst streiten sich zwei Projekte um Port 8081 und um
das Volume mit dem Broker-Schluessel.

- Deploy: [KnovasPlatform/docs/setup.md](KnovasPlatform/docs/setup.md)
- API reference: [docs/KnovasAPI/README.md](docs/KnovasAPI/README.md)

### Adresse hinter Proxys: `PLATFORM_TRUSTED_PROXY_HOPS`

Die Plattform hält zu jeder Sitzung und in den Audit-Einträgen der Experimente
die Adresse fest, von der eine Anfrage kam. Bisher nahm sie dafür den ersten
Eintrag von `X-Forwarded-For` – den jeder Browser selbst setzen kann. Jetzt
zählt sie von rechts so viele Einträge ab, wie Proxys vor ihr stehen und
anhängen: `PLATFORM_TRUSTED_PROXY_HOPS`, in `docker-compose.yml` mit der
Vorgabe `2` (Host-NGINX vor dem `docbridge-web-nginx` des Stacks).

Beim Upgrade:

- Mit Host-NGINX davor, der üblichen Einrichtung, ist nichts zu tun.
- Wer `docbridge-web-nginx` direkt ins LAN stellt (`DOCBRIDGE_WEB_BIND=0.0.0.0`,
  kein Host-NGINX), braucht `PLATFORM_TRUSTED_PROXY_HOPS=1` in `knovas.env` –
  sonst kann jeder Browser die Adresse wählen, mit der er festgehalten wird.
  `./scripts/setup.sh` schreibt diese `1`, wenn `DOCBRIDGE_WEB_BIND` keine
  Loopback-Adresse ist und `knovas.env` noch keinen Wert hat;
  `./scripts/doctor.sh` warnt, wenn dort trotzdem `2` gilt.
- Danach `./scripts/setup.sh && ./scripts/start.sh`.

Einzelheiten: [KnovasPlatform/docs/deployment/host-nginx-internal.md](KnovasPlatform/docs/deployment/host-nginx-internal.md#client-addresses-behind-two-proxies)

### Dokumentverwaltung und Ordner-Zugriffsrechte

Die Verwaltung zeigt jetzt alle hochgeladenen Dokumente des Mandanten und
erlaubt, Zugriffsrechte je Dokument oder je Ordner zu setzen. Ordnerregeln
gelten auch für später eingelesene Dokumente, sodass ein erneuter Abgleich
eine geschlossene Wand nicht wieder öffnet. Beschreibung:
[KnovasPlatform/docs/features/document-administration.md](KnovasPlatform/docs/features/document-administration.md)

### Freigaben

Zugriffsänderungen in der Verwaltung folgen dem Vier-Augen-Prinzip. Ein neuer
Reiter «Freigaben» zeigt, was auf eine zweite Person wartet, und vermerkt jede
Handlung, die ein Administrator allein ausgeführt hat. Da heute alle
abgesicherten Aktionen von Administratoren ausgehen, greift das
Vier-Augen-Prinzip erst im strikten Modus.

### Ingestion in der Verwaltung

Was indexiert wird, wann und hinter welcher Wand, wird jetzt in der Verwaltung
eingestellt — mit Vorschau, Versionen und Wiederherstellung. Der RemoteController
akzeptiert dafür die Anmeldung der Kanzlei selbst.

### Experimente

Ein neues Modul hält Experimente fest — Hypothese, Varianten, Messwerte,
Auswertungen, Entscheidung und Erkenntnis —, für Suchqualität und Technik,
Marketing, Vertrieb und Produkt ebenso wie für selbst angelegte Bereiche. Jedes
Experiment wird zusätzlich als Dokument in Knovas indexiert, sodass die normale
Suche beantwortet, was schon versucht wurde und was dabei herauskam.

Das Modul ist **standardmässig aus** (`EXPERIMENTS_ENABLED=false`). Auch
eingeschaltet sehen es nur Personen mit einer der neuen Rollen `experimenter`
oder `experiments_manager` sowie Administratoren (`admin`, mit allen Rechten
der Verwalter-Rolle, also auch Lesezugriff auf alle Experimente): alle anderen
haben keinen Menüpunkt, keine Experiment-Treffer in der Suche und erhalten auf
jeder Adresse des Moduls «Nicht gefunden». Für bestehende Installationen
ändert sich nichts, solange es aus bleibt; die Tabellen legt die Migration
beim Start trotzdem an.

Einschalten:

1. Unter Verwaltung → Zugriffsgruppen eine Gruppe für Experimente anlegen.
2. In `knovas.env` `EXPERIMENTS_ENABLED=true` und
   `EXPERIMENTS_ACCESS_GROUPS=<Gruppe>` setzen. **Ohne Zugriffsgruppe lädt das
   Modul nichts nach Knovas hoch** — ein Experiment-Dokument ohne Gruppe wäre
   für den ganzen Mandanten sichtbar.
3. `./scripts/setup.sh && ./scripts/start.sh`. Erst die neue Version legt beim
   Start die Rollen `experimenter` und `experiments_manager` an; vorher bietet
   die Verwaltung sie nicht an.
4. Unter Verwaltung → Personen die Rollen vergeben und jeder Person mit einer
   Experimente-Rolle die Gruppe zusätzlich zu ihren bisherigen geben.
5. `./scripts/doctor.sh` prüft den neuen Abschnitt «Experimente» — nach
   Schritt 4, damit die Prüfung «niemand hat die Rolle» den fertigen Stand
   sieht.

- Mitgeliefert sind Pakete für Engineering, Marketing, Vertrieb und Produkt;
  Bereiche, Typen, Metriken und Auswerter sind Konfiguration und lassen sich als
  YAML exportieren und importieren. Das immer installierte Grundpaket bringt
  fünf allgemeine Metriken für jeden Bereich mit (Erfolgsquote, Ereignisse je
  Zeitraum, Dauer in Sekunden, Messwert, Bewertung 1–5), sodass auch ein selbst
  angelegter Bereich sofort messen kann.
- Messwerte kommen von Hand, als CSV oder aus CI: über persönliche
  Zugangsschlüssel und den Python- bzw. Julia-Client unter
  `KnovasPlatform/experiments-sdk/`.
- Die eingebauten Auswertungen rechnen in der Plattform. Eigene Auswerter in
  Python und Julia laufen nur im neuen Dienst `experiments-runner` — ohne
  Netzwerk, erreichbar allein über einen Unix-Socket — und nur mit dem
  Compose-Profil `experiments` (`COMPOSE_PROFILES=experiments`,
  `EXPERIMENTS_RUNNER_URL=unix:///run/experiments-runner/runner.sock`).
  `EXPERIMENTS_RUNNER_CPUS` (Vorgabe 2) darf nicht über der Zahl der CPUs des
  Rechners liegen, sonst legt Docker den Container nicht an. Auf einem Rechner
  mit einer CPU schreibt `setup.sh` `EXPERIMENTS_RUNNER_CPUS=1` in eine
  `knovas.env` ohne eigenen Wert; einen gesetzten Wert prüft `doctor.sh`.
- Alle Daten liegen in der Plattform-Datenbank und sind in deren Sicherung
  enthalten. `python -m experiments purge-index --yes` im Container
  `docbridge-web` entfernt die Knovas-Kopien wieder, auch bei ausgeschaltetem
  Modul.

Beschreibung, Einstellungen und Sicherheitsmodell:
[KnovasPlatform/docs/features/experiments.md](KnovasPlatform/docs/features/experiments.md)

## RemoteController

Discover and sync local text files to Knovas (employee JWT; tenant mTLS for ingestion).

- Deploy: [RemoteController/docs/SETUP.md](RemoteController/docs/SETUP.md)

## Prerequisites (from Knovas)

- Tenant mTLS certificates — each component expects different filenames in a different directory; see [docs/certificates.md](docs/certificates.md)
- Documents indexed in Knovas (via RemoteController or your ingestion pipeline)
- For RemoteController: instance token, employee RC certificates, registered public URL
