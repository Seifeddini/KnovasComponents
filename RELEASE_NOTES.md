# Unreleased

## Dokumentfelder (Dokumentwerte)

Typisierte Werte je Dokument -- Mandant, Zeitraum, Dokumentart, Gericht,
Frist -- als Filter in der Suche, als Liste, auf den Trefferkarten und im
Feldbereich der Vorschau.

- **Voraussetzung: Knovas muss Document Fields fuer den Mandanten
  freischalten.** Bei Knovas ist die Funktion standardmaessig aus, und nichts
  in `knovas.env` schaltet sie ein. Die Plattform fragt Knovas, was es fuer
  den Mandanten anbietet, und zeigt nur das; ohne Freischaltung (oder mit einem
  aelteren Server) bleibt alles wie bisher, ohne neue Oberflaeche und ohne
  neue Schluessel in den Anfragen. *Verwaltung -> System -> Dokumentfelder*
  nennt die Stufe: `aus`, `Werte (ohne Filter)`, `Werte + Liste (Filter in
  der Suche: Kalibrierung bei Knovas fehlt)` oder `Werte + Filter`.
  `./scripts/doctor.sh` prueft dasselbe.
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
- **Was RemoteController sendet:** je Ordner der Ingestion feste Werte
  (`schluessel = Wert; Wert2`), Pfadvorlagen (`{mandant}/{period}/**`) und
  gewaehlte Dateieigenschaften, als Upload-Werte bei jedem Upload. Felder
  blockieren nie die Indexierung: lehnt Knovas sie ab, wird ohne Felder
  indexiert. `RC_DOC_FIELDS=off` schaltet das Senden ab.
- **Was erneutes Senden kostet:** Aendern sich die Felder eines Ordners,
  sendet RemoteController alle seine Dokumente erneut -- jedes ein
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
- Feldwerte, Titel, Pfade und Suchtexte erscheinen in keiner Logzeile, keinem
  Audit-Eintrag und keiner neuen Adresse.
- Umbenennen oder Verschieben einer Datei macht sie bei Knovas zu einem neuen
  Dokument; manuelle Werte des alten werden nicht uebernommen. Ein Downgrade
  der Plattform verwirft die Felder je Ordner; uebertraegt die alte Plattform
  das Profil, loescht RemoteController die Upload-Werte der betroffenen
  Dokumente beim naechsten erneuten Senden.

Anleitung: [KnovasPlatform/docs/features/document-fields.md](KnovasPlatform/docs/features/document-fields.md),
fuer Kunden: [docs/client/document-fields.md](docs/client/document-fields.md),
RemoteController: [RemoteController/CHANGELOG.md](RemoteController/CHANGELOG.md) (0.3.0).

Die API-Referenz `docs/KnovasAPI/Secure_API.md` ist zugunsten des Knovas
Developer Kit stillgelegt (wie zuvor `KnovasPlatform/knovas-docs/`); die
Dokumentfelder stehen nur dort.

## Dokumente in OneDrive und SharePoint (`KNOVAS_DOCUMENTS_URL`)

Statt `KNOVAS_DOCUMENTS_PATH` kann `knovas.env` die Adresse eines OneDrive- oder
SharePoint-Ordners nennen, so wie der Browser sie zeigt, dazu `M365_CLIENT_ID`
und `M365_CLIENT_SECRET` einer Entra-App mit der Anwendungsberechtigung
`Sites.Read.All`. Eine Einstellung fuer beide.

- **Keine Kopie auf dem Server.** RemoteController fragt Microsoft Graph nach
  Aenderungen, laedt nur neue und geaenderte Dateien in ein temporaeres
  Verzeichnis, indexiert sie und loescht sie wieder.
- **Oeffnen und Vorschau in Microsoft 365.** Treffer oeffnen in
  OneDrive/SharePoint; die Vorschau ist der Viewer von Microsoft 365, auf der
  Seite der Fundstelle. Textauszuege und Fundstellen bleiben wie bisher.
- **Uebernahme im Admin-Bereich** zeigt die Unterordner aus OneDrive/SharePoint;
  Zugriffsgruppen je Ordner funktionieren unveraendert.
- Das Client-Secret gelangt nur in den RemoteController-Container, nie in die
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

Behoben dabei: Das Standardprofil von RemoteController uebernahm keine Dateien,
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
