# Einen Standort verbinden

Für Beraterinnen und Berater, die eine Firma an ihr Brain anschliessen. Ein **Standort** ist ein Rechner bei der Firma, auf dem **Knovas Link** läuft und daneben die Komponenten, die ihre Quellen lesen. Nur Knovas Link spricht mit Knovas, und nur von innen nach aussen; die Komponenten sprechen nur mit Knovas Link, auf einem internen Docker-Netz ohne geöffneten Port. Alles wird im Brain Builder eingerichtet und beobachtet.

Stand: MVP des Verbindungsteils (MVP-23 bis MVP-28). Wie Knovas Link sein Zertifikat erhält, hat DEC-0056 entschieden (Option A mit A1): jeder Standort erhält sein eigenes Zertifikat von der Client-CA von Knovas (Abschnitt 2).

Überblick über alle Teile, wer sich wie ausweist und was schon gebaut ist (auf Englisch): [connection-layer.md](connection-layer.md).

## 1. Standort anlegen (Builder › Verbinden)

1. **«Standort anlegen»:** Name (zum Beispiel «Büro Zürich»), verantwortlich, und was dort gelesen wird: GitHub, HTTP/JSON-Schnittstelle, Dateiablage. Dafür braucht es die Berechtigung «Zugänge verwalten».
2. **«Installationspaket herunterladen»:** ein Zip mit `docker-compose.yml`, `knovas-link.env` und `knovas-sources.env`. `knovas-link.env` enthält nur die Adresse der Plattform und ein Einmal-Token für Knovas Link (24 Stunden, nur einmal) und wird nur vom Dienst `knovas-link` gelesen. In `knovas-sources.env` kommen die Zugänge der Quellen; nur die Komponenten lesen sie. Jedes Herunterladen erzeugt ein neues Token; das vorige gilt dann nicht mehr. Läuft Knovas Link schon, enthält das Paket kein Token.
3. **Abbilder:** Der Workflow `release-images` baut `knovas-link` und `sync-component` aus einem Branch, signiert sie und legt sie in GHCR ab ([deployment/release-images.md](deployment/release-images.md)); seine Zusammenfassung nennt `KNOVAS_LINK_DIGEST=sha256:…` und `SYNC_COMPONENT_DIGEST=sha256:…`. Am Standort kommen beide Zeilen in eine Datei `.env` neben `docker-compose.yml`. Das Paket nennt die Abbilder noch ohne Registry; bis der Builder sie aus einer Einstellung nimmt, legt man daneben eine `docker-compose.override.yml` mit dem Namen in GHCR an (`<owner>` in Kleinbuchstaben; nur die Dienste, die das Paket enthält):

   ```yaml
   services:
     knovas-link:
       image: ghcr.io/<owner>/knovas-link@${KNOVAS_LINK_DIGEST:?KNOVAS_LINK_DIGEST in .env}
     sync-component:
       image: ghcr.io/<owner>/sync-component@${SYNC_COMPONENT_DIGEST:?SYNC_COMPONENT_DIGEST in .env}
   ```

   Der Rechner meldet sich mit einem eigenen, widerrufbaren Lese-Token bei GHCR an (`docker login ghcr.io`, Token (classic) nur mit `read:packages`). Ein mit `docker load` kopiertes Abbild hat keinen Digest und startet das Paket nicht.

   **brain-runner 0.3.2 oder neuer:** Hinter Knovas Link braucht die Sync-Komponente diese Version; die Dockerfiles pinnen sie seit dem 10. November 2026 (`BRAIN_RUNNER_GIT_REF`), und der Bau bricht bei einer anderen ab. Mit 0.3.1 plant «bei neuen Commits» bei jedem neuen Commit alle Zuordnungen; mit einer älteren Version (etwa 0.1.1) kann die Komponente hinter Knovas Link weder Probeläufe noch Anwenden noch Schreibaufträge ausführen. Der Release-Lauf nennt die Version im Schritt «Build and check the image» (`brain-runner …`). Steht dort eine ältere Version, das Abbild nicht verwenden (README der Sync-Komponente, «Release coupling»).

## 2. Beim Kunden

```bash
docker compose up -d
```

**Netze und die eine Tür:** Die Komponenten erreichen Knovas Link über das interne Netz `knovas-site`; jeder Dienst ist zusätzlich im Netz `knovas-egress`, damit er seine eigenen Quellen erreicht (GitHub, Microsoft 365, …). Dass nur Knovas Link mit Knovas spricht, sichern die Zugänge, nicht ein gesperrtes Netz: Nur Knovas Link hat ein Zertifikat und ein Token von Knovas; eine Komponente hat keines und kennt nur die lokale Adresse von Knovas Link.

Bei einer Dateiablage den Ordner auf dem Rechner als `KNOVAS_SHARE_FOLDER` angeben; er wird nur lesend eingehängt.

### Der Zertifikatsschritt (DEC-0056, A1)

Jeder Standort hat sein **eigenes Zertifikat**, von der Client-CA von Knovas für den Schlüssel, den Knovas Link bei der Anmeldung erzeugt hat; der Schlüssel verlässt den Rechner nie. Das Secure API der Produktion kann nicht signieren (`/secured/sign_certificate` antwortet 503). Darum setzt die Plattform dort `BUILDER_CONNECTOR_ISSUE_CERTIFICATES=false` (`builder.components.issue_certificates`) und stellt bei der Anmeldung kein Zertifikat aus. Knovas Link meldet sich trotzdem an, wartet aber (`waiting_for_certificate`): Es nimmt keine Aufträge an, sendet keine Probe und leitet nichts an das Brain weiter.

Bei der Anmeldung hält der Builder den **Fingerabdruck des Antrags** fest: SHA-256 des öffentlichen Schlüssels, den Knovas Link erzeugt hat. Die Seite des Standorts zeigt ihn unter «Zertifikat des Standorts». Damit ist der Antrag an diesen Standort gebunden (BB-07, B4; S-4 M2).

1. **Antrag am Standort:**

   ```bash
   docker compose exec -T knovas-link python -m knovas_link certificate-request > site.csr
   docker compose exec -T knovas-link python -m knovas_link certificate-fingerprint
   ```

   Die Datei `site.csr` enthält nur den Zertifikatsantrag, nie den Schlüssel. Sie geht an Knovas. `certificate-fingerprint` zeigt denselben Fingerabdruck wie die Seite des Standorts.
2. **Knovas prüft und signiert den Antrag.** Heute bestätigt und signiert ein Knovas-Admin über `/sign_certificate` des internal-api; später signiert der Ausstellungsjob mit dem Schlüssel im HSM (DEC-0062).
   - **Zuerst vergleichen:** Der Admin prüft, dass der Antrag nur den Namen trägt (`openssl req -in site.csr -noout -subject` zeigt `CN=knovas-link`), und dass sein Fingerabdruck der auf der Seite des Standorts ist:

     ```bash
     openssl req -in site.csr -noout -pubkey | openssl pkey -pubin -outform DER | sha256sum
     ```

     Weicht er ab, wird nicht signiert: Der Antrag kommt nicht von diesem Standort.
   - **Dann signieren, nur im Cluster.** Mit dem Admin-JWT antwortet `/sign_certificate` nur über einen Port-Forward zum internal-api oder `kubectl exec`, nie über die öffentliche Adresse (dort 404, ADR-0005):

     ```bash
     kubectl port-forward deployment/internal-api 5000:5000 -n knovas-prod   # knovas-dev für dev
     curl -s -X POST http://localhost:5000/sign_certificate \
       -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' \
       -d '{"csr": "<Inhalt von site.csr>", "customer_id": "<Client-Id des Mandanten>", "organisation": "<Name der Firma>", "validity_days": 365}'
     ```

     Der Antrag steht in `csr`. Die CA setzt den Mandanten aus `customer_id` (OU `Semantix-Customer-<id>`) und speichert das Zertifikat mit seiner Seriennummer. Die Antwort trägt `certificate`, `certificate_chain`, `serial_number`, `expires_at`. Referenz: im Knovas Employee Kit `api/Certificate_Management.md`.
   - **Im Builder erfassen:** Das `certificate` der Antwort auf der Seite des Standorts unter «Zertifikat erfassen» eintragen (Berechtigung «Zugänge verwalten»). Der Builder nimmt es nur, wenn es zum festgehaltenen Antrag gehört, den Mandanten dieser Plattform nennt, für die Anmeldung als Client ausgestellt und jetzt gültig ist und, wo die Client-CA eingestellt ist (`BUILDER_CONNECTOR_CLIENT_CA`), von ihr signiert ist. Sonst nennt er den Grund und hält nichts fest. Erfasst, ist sein Fingerabdruck der, den die Plattform von Knovas Link verlangt.
   - Das `certificate` (optional mit der Kette) geht als `site.crt` an den Standort zurück. Signiert später der Ausstellungsjob, ändert sich am Standort nichts.
3. **Installieren am Standort:**

   ```bash
   docker compose exec -T knovas-link python -m knovas_link install-certificate < site.crt
   ```

   Knovas Link nimmt das Zertifikat nur, wenn es zu seinem Schlüssel gehört, jetzt gültig ist, für die Anmeldung als Client ausgestellt ist (`clientAuth`), von der Client-CA von Knovas signiert ist und den Mandanten des Standorts nennt; sonst schreibt es nichts und nennt den Grund als Code (`certificate.wrong_key`, `certificate.not_valid_now`, `certificate.not_client_auth`, `certificate.wrong_issuer`, `certificate.wrong_tenant`, `certificate.unreadable`, `certificate.not_enrolled`), auf Deutsch und Englisch. Client-CA und Mandant nennt die Plattform bei der Anmeldung; wo sie es nicht tut, setzt man `LINK_CLIENT_CA` (Datei) und `LINK_TENANT` in `knovas-link.env`. Kennt Knovas Link eines von beiden nicht, installiert es nichts (`certificate.no_client_ca`, `certificate.no_tenant`). Bei Erfolg nennt es die Seriennummer.

**Der TLS-Terminator vor der Plattform** prüft das Zertifikat von Knovas Link gegen die Client-CA und setzt `X-Client-Cert-Fingerprint`: mit host nginx `scripts/host-https.sh <fqdn> <E-Mail> <Client-CA als PEM>` (das njs-Modul `libnginx-mod-http-js` muss installiert sein; `deploy/host-nginx/knovas-client-cert.js`). Ohne ihn bleibt Knovas Link nach dem Erfassen rot (`certificate_required`).

**Bis das Zertifikat erfasst ist** (mit `BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=true`, dem Standard), nimmt die Plattform von Knovas Link nur den Herzschlag an, ohne ihm Befehle zu geben; Konfiguration, Paket, Befehle und Berichte verweigert sie (`certificate_not_recorded`). Danach verlangt sie bei jedem Aufruf den Fingerabdruck des erfassten Zertifikats, den der TLS-Terminator vor der Plattform setzt (BB-07, B3).

**Kein Neustart nötig:** Knovas Link übernimmt das Zertifikat beim nächsten Heartbeat (standardmässig nach höchstens 30 Sekunden); die Zeile «Zertifikat» wird grün, danach «Jetzt prüfen». Ersetzt ein neues Zertifikat eines, das Knovas Link schon verwendet (Erneuerung vor dem Ablauf), gilt es erst nach `docker compose restart knovas-link`; der Befehl sagt das. `-T` gehört dazu: Ohne es öffnet docker compose ein Terminal, das die Zeilenenden des Antrags verändert und die Eingabe aus `site.crt` ablehnt.

**Sperren:** Eine Administratorin oder ein Administrator von Knovas sperrt das Zertifikat über `POST /revoke_certificate` des internal-api mit seiner Seriennummer. Danach weist das Secure API den Standort ab; nur dieser Standort hält an. Nach «Token erneuern» meldet sich Knovas Link mit einem neuen Schlüssel an; der Zertifikatsschritt ist dann zu wiederholen.

Die Seite des Standorts hakt ab: **Paket installiert · Zertifikat · Knovas Link online · Komponente gemeldet · Arbeitskanal geprüft**. Jede offene Zeile sagt, was zu tun ist. «Jetzt prüfen» schickt eine Probe über den Arbeitskanal; «Token erneuern» lässt Knovas Link sich neu anmelden (danach das Paket neu herunterladen).

## 3. Quelle anlegen (Builder › Quellen & Zuordnung)

Die Quelle auf die Komponente des Standorts legen. Das darf nur, wer die Standorte des Mandanten verwaltet (Berechtigung «Zugänge verwalten»): eine Beraterin mit einem Zugang zu nur einem Arbeitsbereich oder eine Autorin ohne diese Berechtigung kann keine Quelle an die Komponente eines Standorts binden, auch nicht über einen Import oder «Wiederherstellen» (BB-07, B2). Ihr Zugang wird **nicht** in die Plattform eingegeben: Der Builder zeigt die genaue Zeile für `knovas-sources.env`, etwa `SYNC_CREDENTIAL_AKTEN=`, mit «Kopieren». Den Wert am Standort dahinter eintragen, neu starten und «Verbindung prüfen».

**Eine Art von Quelle, für die der Builder kein eigenes Formular hat** (Protokoll 2, Entwurf; nur mit `BUILDER_PROTOCOL_V2=true`, Standard aus; SPEC-write-back-13): Meldet ein Connector am Standort eine Art von Quelle an, die der Builder nicht kennt (etwa der Jira-Connector die Art `jira`), steht sie im Dialog «Quelle hinzufügen» unter «Von einem Connector am Standort angemeldet». Der Connector darf sich bei Knovas Link unter seiner eigenen Art anmelden; die Plattform führt ihn, solange seine Anmeldung dem Protokoll entspricht, sonst nicht (wie bisher). Zuordnen lässt sich eine solche Quelle im Builder noch nicht (J-4). Angeboten wird sie nur auf den Komponenten, die sie angemeldet und die Prüfungen fürs Lesen bestanden haben (`replay_changes_nothing`, `deletion_guard`, `no_credential_leaves_site`). Dazu gehören:

- **Schlüssel:** bestimmt die Zeile für den Zugang am Standort (`SYNC_CREDENTIAL_<SCHLÜSSEL>=`); leer gelassen, wird er aus dem Namen gebildet.
- **Einstellungen als Name und Wert**, so wie der Connector sie liest; welche, sagt seine Dokumentation. Ein Wert ist Text, eine Zahl, Ja/Nein oder eine Liste von Texten (mit Komma getrennt). Beim Jira-Connector etwa `site` (`https://<firma>.atlassian.net`) und `project` (der Schlüssel des Projekts), falls nötig `fields` und `computed_fields` (Listen von Feld-Ids) und `page_size` (Zahl). Namen klein geschrieben, aus Buchstaben, Ziffern und `_`.
- **Kein Zugang:** Ein Name oder Wert, der nach einem Zugang aussieht (Token, Passwort, Geheimnis, Schlüssel, eine Adresse mit Benutzer oder Token, «E-Mail:Token»), wird abgelehnt; die Meldung nennt die Zeile für `knovas-sources.env`. Der Zugang wird am Standort eingetragen, nie in die Plattform. Die Prüfung erkennt Zugänge an ihrer Form, nicht jedes Geheimnis: ein kurzes Passwort unter einem harmlosen Namen fiele nicht auf. Es gehört trotzdem nicht hierher.

Geändert werden die Einstellungen auf der Seite der Quelle unter «Einstellungen ändern»; wie jede Änderung erreichen sie den Standort erst nach Prüfen & Veröffentlichen, dann genau so, wie sie eingetragen sind. «Änderungen dürfen zurückgeschrieben werden» gibt es, wo der Connector Aktionen angemeldet und die Prüfungen fürs Schreiben bestanden hat; «Agenten dürfen Originale abrufen», wo er `fetch` angemeldet hat; die Schreibweise wie bei jeder Quelle (Abschnitt 5). «Testen» liest eine solche Quelle nicht: das tut nur die Komponente am Standort.

**Der Standort läuft mit dem veröffentlichten Stand, nie mit dem Entwurf** (BB-07, B2). Knovas Link und seine Komponenten erhalten Quellen, ihre Einstellungen (Adresse, Repository), ihre Schalter und die Zuordnungen nur aus der zuletzt veröffentlichten Fassung. Eine Änderung im Entwurf, etwa eine neue Adresse einer HTTP/JSON-Quelle, erreicht den Standort erst nach Probelauf, Freigabe und Veröffentlichen; vorher geht der Zugang des Standorts nie an eine Adresse, die niemand geprüft hat. Auch «Anwenden» auf einer Komponente wendet den veröffentlichten Stand an. Eine neue Quelle wird darum zuerst veröffentlicht; erst dann prüft die Komponente sie, und erst dann lässt sich ihre Zuordnung an der Form der Dateien entwerfen.

Zwei Schalter je Quelle, beide aus, bis jemand sie einschaltet:

- **«Agenten dürfen Originale abrufen»**: Agenten erhalten die Datei, wie die Quelle sie hält, nie den indexierten Text an ihrer Stelle.
- **«Änderungen dürfen zurückgeschrieben werden»**: nur, wo die Art der Quelle es kann (GitHub, als Pull Request) und die Komponente es meldet (`write:github`). Das Token braucht dann «Contents: write» und «Pull requests: write».

Beide sind Änderungen des Entwurfs: Sie werden geprüft und veröffentlicht wie alles andere und erreichen die Komponente erst danach.

## 4. Zuordnung: «Zugriff» und «Zurückschreiben»

- **«Zugriff»:** «Wie die Ordnerregel», «Alle im Mandanten» oder Gruppen aus dem Zugriffsentwurf. Nichts ist vorgewählt. «Alle im Mandanten» überschreibt Ordnerregeln; der Probelauf hält an, wo eine Ordnerregel gilt. «Aus der Quelle» kommt mit der Berechtigungsübernahme.
- **«Zurückschreiben»:** je Attribut ein Schalter, wo der Wert ein einfaches Feld der Quelle ist. Mit Schritten gebaute Werte und berechnete Attribute lassen sich nicht zurückschreiben; der Grund steht daneben. Ausgeschaltet, solange der Schreibschalter der Quelle aus ist.
- **«Was diese Zuordnung tut»** fasst die Zuordnung in einem Satz zusammen.

Dann wie immer: **Prüfen & Veröffentlichen** (Probelauf, Freigabe, Veröffentlichen).

## 5. Im Betrieb

- **Betrieb › Freigaben:** Änderungsvorschläge von Agenten oder Personen annehmen oder mit Begründung ablehnen (Berechtigung «Veröffentlichen»). Die Zeile folgt dem Ergebnis: in Arbeit, Pull Request mit Link, Konflikt, abgelehnt. Wer vorschlägt und für wen, steht so da, wie es angegeben wurde; geprüft wird es erst mit einer späteren Stufe.
- **Betrieb › Freigaben, Änderungssätze** (Protokoll 2, Entwurf; nur mit `BUILDER_PROTOCOL_V2=true`, Standard aus): Benannte Aktionen (etwa «Status setzen») mit der Vorschau, die die Komponente am Standort berechnet hat, ohne zu schreiben: je Feld der Wert vorher und nachher, die gelesene Version, genau oder nicht. «Freigeben» gibt es nur, wenn jede Vorschau genau ist; geschrieben wird mit der **Schreibweise der Quelle** (Quellen & Zuordnung › Quelle › Aktionen der Komponente: «direkt nach der Freigabe in Knovas», der Standard, oder «als Vorschlag zur Prüfung in der Quelle», wie jeder Schalter erst nach Prüfen & Veröffentlichen wirksam). «Ablehnen» braucht eine Begründung. Berechtigung «Veröffentlichen».
- **Zeitplan «Bei neuen Commits»** (Seite der Komponente › Zeitplan, Berechtigung «Zugänge verwalten»): Die Komponente fragt alle 15 bis 3600 Sekunden (Standard 30) jedes GitHub-Repository nach seinem neuesten Commit. Ist er schon abgeglichen, geschieht nichts weiter. Ist er neu, plant sie nur die Zuordnungen, die der Commit betrifft; der Probelauf erscheint wie jeder andere. Daneben: «Manuell», «Regelmäßig» (ab jeder Minute), «Im Zeitfenster». Knovas Link selbst hat keinen Zeitplan; jede Komponente am Standort hat ihren eigenen.
- **«Automatisch anwenden, wenn nichts zur Freigabe ansteht»** (Seite des Standorts › Quellen und Abgleich, je Quelle; Berechtigung «Zugänge verwalten»; Standard aus): Ein geplanter Lauf wird gleich angewendet, nur wenn nichts darin auf eine Person wartet. Was der Löschschutz anhält, Abweichungen im Brain, Einträge zum Stilllegen, Freigaben von Zuordnungen und angehaltene oder gemeldete Dokumente bleiben bei der Komponente unter «Zurückgehaltene Pläne» und «Jetzt anwenden». Ein Probelauf, den jemand startet, wird nie von selbst angewendet. Jede Änderung steht als Ereignis bei der Komponente und im Zugriffsprotokoll.
- **Seite des Standorts › Quellen und Abgleich** (und die Liste «Standorte» unter «Verbinden»): je Quelle der zuletzt abgeglichene Commit (kurz, mit Link auf GitHub), wann, was der letzte Lauf schrieb (nur Zahlen), die nächste Prüfung, der Zeitplan und ob automatisch angewendet wird.
- **Betrieb › Verbindung:** je Standort eine Karte. Grün nur, wenn in den letzten 10 Minuten eine Probe über den Arbeitskanal ging; was die Plattform nicht weiss, steht als «unbekannt» da. Darunter die letzten 24 Stunden und die Alarme, jeder mit dem, was zu tun ist. Die Karte liest sich alle 30 Sekunden neu.

## Wenn etwas rot ist

| Meldung | Was tun |
|---|---|
| Knovas Link nicht erreichbar | Am Standort `docker compose ps`; erreicht der Rechner Knovas ausgehend? Danach «Jetzt prüfen». |
| Arbeitskanal unterbrochen | Ausgehende Verbindung zu Knovas prüfen (Firewall, Proxy). |
| Wartet auf das Zertifikat | Den Zertifikatsschritt ausführen (Abschnitt 2): Antrag mit `certificate-request`, Fingerabdruck vergleichen, von Knovas signieren lassen, im Builder erfassen, mit `install-certificate` installieren. |
| Quelle nicht lesbar | Die Seite der Komponente nennt je Quelle den Grund; Zeile in `knovas-sources.env` prüfen, neu starten, «Verbindung prüfen». |
| Vorschlag wartet | Betrieb › Freigaben. |

Jeder Code hat im Builder einen Titel, eine Erklärung und den nächsten Schritt.
