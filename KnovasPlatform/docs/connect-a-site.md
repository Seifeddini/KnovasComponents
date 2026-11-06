# Einen Standort verbinden

Für Beraterinnen und Berater, die eine Firma an ihr Brain anschliessen. Ein **Standort** ist ein Rechner bei der Firma, auf dem **Knovas Link** läuft und daneben die Komponenten, die ihre Quellen lesen. Nur Knovas Link spricht mit Knovas, und nur von innen nach aussen; die Komponenten sprechen nur mit Knovas Link, auf einem internen Docker-Netz ohne geöffneten Port. Alles wird im Brain Builder eingerichtet und beobachtet.

Stand: MVP des Verbindungsteils (MVP-23 bis MVP-28). Wie Knovas Link sein Zertifikat erhält, hat DEC-0056 entschieden (Option A mit A1): jeder Standort erhält sein eigenes Zertifikat von der Client-CA von Knovas (Abschnitt 2).

## 1. Standort anlegen (Builder › Verbinden)

1. **«Standort anlegen»:** Name (zum Beispiel «Büro Zürich»), verantwortlich, und was dort gelesen wird: GitHub, HTTP/JSON-Schnittstelle, Dateiablage. Dafür braucht es die Berechtigung «Zugänge verwalten».
2. **«Installationspaket herunterladen»:** ein Zip mit `docker-compose.yml`, `knovas-link.env` und `knovas-sources.env`. `knovas-link.env` enthält nur die Adresse der Plattform und ein Einmal-Token für Knovas Link (24 Stunden, nur einmal) und wird nur vom Dienst `knovas-link` gelesen. In `knovas-sources.env` kommen die Zugänge der Quellen; nur die Komponenten lesen sie. Jedes Herunterladen erzeugt ein neues Token; das vorige gilt dann nicht mehr. Läuft Knovas Link schon, enthält das Paket kein Token.
3. **Abbilder:** Es gibt noch keine Registry. Die Abbilder `knovas-link` und `sync-component` werden aus dem Repository gebaut und auf den Rechner kopiert (`docker save` / `docker load`). Die Datei nennt sie per Digest; die Variablen `KNOVAS_LINK_DIGEST` und `SYNC_COMPONENT_DIGEST` sind Platzhalter, bis Knovas Abbilder mit Digest veröffentlicht.

## 2. Beim Kunden

```bash
docker compose up -d
```

**Netze und die eine Tür:** Die Komponenten erreichen Knovas Link über das interne Netz `knovas-site`; jeder Dienst ist zusätzlich im Netz `knovas-egress`, damit er seine eigenen Quellen erreicht (GitHub, Microsoft 365, …). Dass nur Knovas Link mit Knovas spricht, sichern die Zugänge, nicht ein gesperrtes Netz: Nur Knovas Link hat ein Zertifikat und ein Token von Knovas; eine Komponente hat keines und kennt nur die lokale Adresse von Knovas Link.

Bei einer Dateiablage den Ordner auf dem Rechner als `KNOVAS_SHARE_FOLDER` angeben; er wird nur lesend eingehängt.

### Der Zertifikatsschritt (DEC-0056, A1)

Jeder Standort hat sein **eigenes Zertifikat**, von der Client-CA von Knovas für den Schlüssel, den Knovas Link bei der Anmeldung erzeugt hat; der Schlüssel verlässt den Rechner nie. Das Secure API der Produktion kann nicht signieren (`/secured/sign_certificate` antwortet 503). Darum setzt die Plattform dort `BUILDER_CONNECTOR_ISSUE_CERTIFICATES=false` (`builder.components.issue_certificates`) und stellt bei der Anmeldung kein Zertifikat aus. Knovas Link meldet sich trotzdem an, wartet aber (`waiting_for_certificate`): Es nimmt keine Aufträge an, sendet keine Probe und leitet nichts an das Brain weiter.

1. **Antrag am Standort:**

   ```bash
   docker compose exec -T knovas-link python -m knovas_link certificate-request > site.csr
   ```

   Die Datei `site.csr` enthält nur den Zertifikatsantrag, nie den Schlüssel. Sie geht an Knovas.
2. **Knovas signiert den Antrag.** Heute bestätigt und signiert ein Knovas-Admin über `/sign_certificate` des internal-api; später signiert der Ausstellungsjob mit dem Schlüssel im HSM (DEC-0062). Der Admin prüft zuerst, dass der Antrag nur den Namen trägt (`openssl req -in site.csr -noout -subject` zeigt `CN=knovas-link`), und signiert ihn dann über `POST /sign_certificate` (JWT, mit der `customer_id` des Mandanten; die CA setzt den Mandanten daraus). Das Zertifikat wird mit seiner Seriennummer gespeichert. Das `certificate` der Antwort geht als `site.crt` an den Standort zurück. Signiert später der Ausstellungsjob, ändert sich am Standort nichts.
3. **Installieren am Standort:**

   ```bash
   docker compose exec -T knovas-link python -m knovas_link install-certificate < site.crt
   ```

   Knovas Link nimmt das Zertifikat nur, wenn es zu seinem Schlüssel gehört, jetzt gültig ist und für die Anmeldung als Client ausgestellt ist (`clientAuth`); sonst schreibt es nichts und nennt den Grund als Code (`certificate.wrong_key`, `certificate.not_valid_now`, `certificate.not_client_auth`, `certificate.unreadable`, `certificate.not_enrolled`), auf Deutsch und Englisch. Bei Erfolg nennt es die Seriennummer.

**Kein Neustart nötig:** Knovas Link übernimmt das Zertifikat beim nächsten Heartbeat (standardmässig nach höchstens 30 Sekunden); die Zeile «Zertifikat» wird grün, danach «Jetzt prüfen». Ersetzt ein neues Zertifikat eines, das Knovas Link schon verwendet (Erneuerung vor dem Ablauf), gilt es erst nach `docker compose restart knovas-link`; der Befehl sagt das. `-T` gehört dazu: Ohne es öffnet docker compose ein Terminal, das die Zeilenenden des Antrags verändert und die Eingabe aus `site.crt` ablehnt.

**Sperren:** Eine Administratorin oder ein Administrator von Knovas sperrt das Zertifikat über `POST /revoke_certificate` des internal-api mit seiner Seriennummer. Danach weist das Secure API den Standort ab; nur dieser Standort hält an. Nach «Token erneuern» meldet sich Knovas Link mit einem neuen Schlüssel an; der Zertifikatsschritt ist dann zu wiederholen.

Die Seite des Standorts hakt ab: **Paket installiert · Zertifikat · Knovas Link online · Komponente gemeldet · Arbeitskanal geprüft**. Jede offene Zeile sagt, was zu tun ist. «Jetzt prüfen» schickt eine Probe über den Arbeitskanal; «Token erneuern» lässt Knovas Link sich neu anmelden (danach das Paket neu herunterladen).

## 3. Quelle anlegen (Builder › Quellen & Zuordnung)

Die Quelle auf die Komponente des Standorts legen. Das darf nur, wer die Standorte des Mandanten verwaltet (Berechtigung «Zugänge verwalten»): eine Beraterin mit einem Zugang zu nur einem Arbeitsbereich oder eine Autorin ohne diese Berechtigung kann keine Quelle an die Komponente eines Standorts binden, auch nicht über einen Import oder «Wiederherstellen» (BB-07, B2). Ihr Zugang wird **nicht** in die Plattform eingegeben: Der Builder zeigt die genaue Zeile für `knovas-sources.env`, etwa `SYNC_CREDENTIAL_AKTEN=`, mit «Kopieren». Den Wert am Standort dahinter eintragen, neu starten und «Verbindung prüfen».

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
- **Betrieb › Verbindung:** je Standort eine Karte. Grün nur, wenn in den letzten 10 Minuten eine Probe über den Arbeitskanal ging; was die Plattform nicht weiss, steht als «unbekannt» da. Darunter die letzten 24 Stunden und die Alarme, jeder mit dem, was zu tun ist. Die Karte liest sich alle 30 Sekunden neu.

## Wenn etwas rot ist

| Meldung | Was tun |
|---|---|
| Knovas Link nicht erreichbar | Am Standort `docker compose ps`; erreicht der Rechner Knovas ausgehend? Danach «Jetzt prüfen». |
| Arbeitskanal unterbrochen | Ausgehende Verbindung zu Knovas prüfen (Firewall, Proxy). |
| Wartet auf das Zertifikat | Den Zertifikatsschritt ausführen (Abschnitt 2): Antrag mit `certificate-request`, von Knovas signieren lassen, mit `install-certificate` installieren. |
| Quelle nicht lesbar | Die Seite der Komponente nennt je Quelle den Grund; Zeile in `knovas-sources.env` prüfen, neu starten, «Verbindung prüfen». |
| Vorschlag wartet | Betrieb › Freigaben. |

Jeder Code hat im Builder einen Titel, eine Erklärung und den nächsten Schritt.
