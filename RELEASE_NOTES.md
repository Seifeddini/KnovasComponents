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
   Einmalpasswort steht in `/run/platform-admin-bootstrap` im Container
   `docbridge-web`. Danach anmelden, Passwort aendern, Datei loeschen.
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

## RemoteController

Discover and sync local text files to Knovas (employee JWT; tenant mTLS for ingestion).

- Deploy: [RemoteController/docs/SETUP.md](RemoteController/docs/SETUP.md)

## Prerequisites (from Knovas)

- Tenant mTLS certificates — each component expects different filenames in a different directory; see [docs/certificates.md](docs/certificates.md)
- Documents indexed in Knovas (via RemoteController or your ingestion pipeline)
- For RemoteController: instance token, employee RC certificates, registered public URL
