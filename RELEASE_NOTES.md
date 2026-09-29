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
  YAML exportieren und importieren.
- Messwerte kommen von Hand, als CSV oder aus CI: über persönliche
  Zugangsschlüssel und den Python- bzw. Julia-Client unter
  `KnovasPlatform/experiments-sdk/`.
- Die eingebauten Auswertungen rechnen in der Plattform. Eigene Auswerter in
  Python und Julia laufen nur im neuen Dienst `experiments-runner` — ohne
  Netzwerk, erreichbar allein über einen Unix-Socket — und nur mit dem
  Compose-Profil `experiments` (`COMPOSE_PROFILES=experiments`,
  `EXPERIMENTS_RUNNER_URL=unix:///run/experiments-runner/runner.sock`).
  `EXPERIMENTS_RUNNER_CPUS` (Vorgabe 2) darf nicht über der Zahl der CPUs des
  Rechners liegen, sonst legt Docker den Container nicht an; auf einem Rechner
  mit einer CPU `EXPERIMENTS_RUNNER_CPUS=1` setzen (`doctor.sh` prüft das).
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
