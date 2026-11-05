# Einen Standort verbinden

Für Beraterinnen und Berater, die eine Firma an ihr Brain anschliessen. Ein **Standort** ist ein Rechner bei der Firma, auf dem **Knovas Link** läuft und daneben die Komponenten, die ihre Quellen lesen. Nur Knovas Link spricht mit Knovas, und nur von innen nach aussen; die Komponenten sprechen nur mit Knovas Link, auf einem internen Docker-Netz ohne geöffneten Port. Alles wird im Brain Builder eingerichtet und beobachtet.

Stand: MVP des Verbindungsteils (MVP-23 bis MVP-28). Wie Knovas Link sein Zertifikat erhält, entscheidet DEC-0056; bis dahin ist dieser Schritt offen.

## 1. Standort anlegen (Builder › Verbinden)

1. **«Standort anlegen»:** Name (zum Beispiel «Büro Zürich»), verantwortlich, und was dort gelesen wird: GitHub, HTTP/JSON-Schnittstelle, Dateiablage. Dafür braucht es die Berechtigung «Zugänge verwalten».
2. **«Installationspaket herunterladen»:** ein Zip mit `docker-compose.yml` und `knovas-site.env`. Die zweite Datei enthält nur die Adresse der Plattform und ein Einmal-Token für Knovas Link (24 Stunden, nur einmal). Jedes Herunterladen erzeugt ein neues Token; das vorige gilt dann nicht mehr. Läuft Knovas Link schon, enthält das Paket kein Token.
3. **Abbilder:** Es gibt noch keine Registry. Die Abbilder `knovas-link` und `sync-component` werden aus dem Repository gebaut und auf den Rechner kopiert (`docker save` / `docker load`). Die Datei nennt sie per Digest; die Variablen `KNOVAS_LINK_DIGEST` und `SYNC_COMPONENT_DIGEST` sind Platzhalter, bis Knovas Abbilder mit Digest veröffentlicht.

## 2. Beim Kunden

```bash
docker compose --env-file knovas-site.env up -d
```

Danach der **Zertifikatsschritt**: wie in DEC-0056 entschieden; bis dahin wartet Knovas Link (`waiting_for_certificate`) und nimmt keine Aufträge an. Bei einer Dateiablage den Ordner auf dem Rechner als `KNOVAS_SHARE_FOLDER` angeben; er wird nur lesend eingehängt.

Die Seite des Standorts hakt ab: **Paket installiert · Zertifikat · Knovas Link online · Komponente gemeldet · Arbeitskanal geprüft**. Jede offene Zeile sagt, was zu tun ist. «Jetzt prüfen» schickt eine Probe über den Arbeitskanal; «Token erneuern» lässt Knovas Link sich neu anmelden (danach das Paket neu herunterladen).

## 3. Quelle anlegen (Builder › Quellen & Zuordnung)

Die Quelle auf die Komponente des Standorts legen. Ihr Zugang wird **nicht** in die Plattform eingegeben: Der Builder zeigt die genaue Zeile für `knovas-site.env`, etwa `SYNC_CREDENTIAL_AKTEN=`, mit «Kopieren». Den Wert am Standort dahinter eintragen, neu starten und «Verbindung prüfen».

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
| Wartet auf das Zertifikat | Den Zertifikatsschritt nach DEC-0056 ausführen. |
| Quelle nicht lesbar | Die Seite der Komponente nennt je Quelle den Grund; Zeile in `knovas-site.env` prüfen, neu starten, «Verbindung prüfen». |
| Vorschlag wartet | Betrieb › Freigaben. |

Jeder Code hat im Builder einen Titel, eine Erklärung und den nächsten Schritt.
