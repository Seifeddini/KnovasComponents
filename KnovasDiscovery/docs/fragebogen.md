# Fragebogen zur technischen Bestandsaufnahme

Für das erste Gespräch mit der Kanzlei und ihrem IT-Betreuer (45 Minuten). Wir füllen ihn im Gespräch selbst aus.
Jede Antwort landet in `intake.yaml`; die Spalte rechts sagt wo. Entwurf vom 20. November 2026; die Einwilligung im
Anhang ist vor der ersten Verwendung juristisch zu prüfen.

Zu Beginn sagen: Wir lesen bei der Bestandsaufnahme keine Inhalte. Wir zählen und vermessen: wie viele Dateien,
welche Formate, welche Tabellen, wie viele Benutzer. Namen von Mandanten, Dateien und Personen verlassen Ihre
Kanzlei nicht.

## A. Die Kanzlei

| Nr. | Frage | Antwort | `intake.yaml` |
|-----|-------|---------|---------------|
| A1 | Wie viele Personen arbeiten in der Kanzlei, davon wie viele Anwältinnen und Anwälte? | | `client.people`, `client.lawyers` |
| A2 | Wie viele Standorte? Arbeiten alle auf denselben Systemen? | | Notiz |
| A3 | Wer betreut die Informatik: jemand im Haus oder ein externer Dienstleister? Name und Kontakt. | | `client.it_provider` |
| A4 | Wer entscheidet in der Kanzlei über Zugriffe auf Daten? | | Notiz |
| A5 | Steht in den nächsten zwölf Monaten ein Wechsel an (neue Kanzleisoftware, Umzug zu Microsoft 365, neuer IT-Betreuer)? | | Notiz |

## B. Software

Für jede Zeile: Produkt, Version falls bekannt, wo es läuft (Server in der Kanzlei, beim IT-Betreuer, Cloud des
Herstellers).

| Nr. | Frage | Antwort | `intake.yaml` |
|-----|-------|---------|---------------|
| B1 | Welche Kanzleisoftware führen Sie (Akten, Adressen, Leistungen, Fristen)? | | `software` (category: practice) |
| B2 | Womit schreiben und versenden Sie E-Mails? Exchange im Haus, Microsoft 365, etwas anderes? | | `software` (category: mail) |
| B3 | Nutzen Sie Microsoft 365 über E-Mail hinaus: SharePoint, OneDrive, Teams, Copilot? | | `software`, `targets.m365` |
| B4 | Gibt es ein Dokumentenmanagementsystem neben der Kanzleisoftware? | | `software` (category: dms) |
| B5 | Womit führen Sie die Buchhaltung und die Löhne? | | `software` (category: finance) |
| B6 | Womit erfassen Sie Zeit, falls nicht in der Kanzleisoftware? | | `software` |
| B7 | Diktieren Sie? Mit welchem System? | | `software` |
| B8 | Gibt es weitere Systeme, in denen Wissen der Kanzlei liegt (Wissensdatenbank, Intranet, Vorlagenverwaltung)? | | `software` |

## C. Wo die Daten liegen

Nicht nach Systemen fragen, sondern nach Wegen:

| Nr. | Frage | Antwort | `intake.yaml` |
|-----|-------|---------|---------------|
| C1 | Ein Brief kommt per Post. Was geschieht damit, bis er in der Akte ist? | | Notiz, `targets.fileshares` |
| C2 | Eine E-Mail zu einem Mandat kommt an. Wird sie in der Akte abgelegt? Immer, manchmal, von wem? | | Notiz |
| C3 | Sie schreiben eine Rechtsschrift. Wo liegt der Entwurf, wo die Endfassung? | | `targets.fileshares` |
| C4 | Ein Mandat ist abgeschlossen. Was geschieht mit der Akte? Wo liegen Akten von vor zehn Jahren? | | Notiz |
| C5 | Welche Laufwerke sehen die Mitarbeitenden (Buchstaben und Namen)? Was liegt auf welchem? | | `targets.fileshares` |
| C6 | Wo liegen Vorlagen, Weisungen, Checklisten der Kanzlei? | | `targets.fileshares.roots` |
| C7 | Gibt es Ordner, die Knovas nie sehen darf (Personal, Löhne, Partner, Privates)? | | Notiz: ausserhalb des Umfangs |
| C8 | Wie gross ist die Ablage ungefähr, und seit wann wird sie geführt? | | Notiz |

## D. Benutzer und Rechte

| Nr. | Frage | Antwort | `intake.yaml` |
|-----|-------|---------|---------------|
| D1 | Sieht heute jede Person jede Akte? | | Notiz |
| D2 | Gibt es Akten, die nur ein Team öffnen kann? Wo ist das eingestellt: in der Kanzleisoftware oder auf dem Laufwerk? | | Notiz |
| D3 | Gibt es Personen, die von einzelnen Akten ausgeschlossen sind (Interessenkonflikt)? | | Notiz |
| D4 | Was darf das Sekretariat nicht sehen? | | Notiz |
| D5 | Wie melden sich die Mitarbeitenden an: Windows-Konto, Microsoft-Konto? | | `targets.directories`, `targets.m365` |
| D6 | Wer richtet neue Mitarbeitende ein und sperrt Ausgetretene? | | Notiz |

## E. Betrieb und Zugang (mit dem IT-Betreuer)

| Nr. | Frage | Antwort | `intake.yaml` |
|-----|-------|---------|---------------|
| E1 | Wo steht der Dateiserver, und unter welcher Adresse ist die Freigabe erreichbar? | | `targets.fileshares` |
| E2 | Gibt es ein Active Directory? Wird es mit Microsoft 365 abgeglichen? | | `targets.directories` |
| E3 | Auf welchem Server läuft die Datenbank der Kanzleisoftware (Server, Instanz, Port)? | | `targets.databases` |
| E4 | Können Sie ein Dienstkonto einrichten, das die vereinbarten Ordner nur lesen kann? | | `targets.*` |
| E5 | Können Sie eine Datenbank-Anmeldung einrichten, die nur lesen kann? Erlaubt der Vertrag mit dem Hersteller das? | | `targets.databases` |
| E6 | Wie kommen wir für die Bestandsaufnahme hinein: VPN, Fernwartung, vor Ort? | | Notiz |
| E7 | Auf welcher Maschine kann später Knovas laufen (Docker, 4 Kerne, 8 GB)? Darf sie nach aussen eine Verbindung zu Knovas aufbauen? | | `knovas` |
| E8 | Gibt es einen Proxy oder eine Firewall, die ausgehende Verbindungen einschränkt? | | Notiz |
| E9 | Wie und wohin wird gesichert? | | Notiz |

## F. Erwartungen

| Nr. | Frage | Antwort |
|-----|-------|---------|
| F1 | Nennen Sie drei Fragen, die Knovas Ihnen beantworten soll. | |
| F2 | Was wäre für Sie ein Grund, Knovas nach drei Monaten wieder abzuschalten? | |
| F3 | Welche Daten sind für diese Fragen unverzichtbar, welche wären schön? | |
| F4 | Bis wann soll die Suche über die Akten stehen? | |

## Anhang: Einwilligung zur technischen Bestandsaufnahme (Entwurf, juristisch zu prüfen)

> Die Kanzlei ___ beauftragt die Knovas GmbH mit einer technischen Bestandsaufnahme ihrer Informatiksysteme zur
> Vorbereitung der Einführung von Knovas.
>
> 1. **Umfang.** Untersucht werden ausschliesslich die Systeme, die in der beiliegenden Liste genannt sind.
> 2. **Art des Zugriffs.** Der Zugriff erfolgt nur lesend, über ein von der Kanzlei oder ihrem IT-Betreuer
>    eingerichtetes Konto. Es werden keine Dokumente geöffnet und keine Datensätze der Kanzleisoftware gelesen.
>    Erhoben werden Mengen, Dateiformate, Ordnerstrukturen, Tabellen- und Feldnamen sowie die Anzahl der Benutzer
>    und Gruppen.
> 3. **Was die Kanzlei verlässt.** Der Bericht enthält Zahlen, Muster und technische Bezeichnungen. Er enthält
>    keine Namen von Mandanten, Personen, Dateien oder Ordnern. Eine Detaildatei mit solchen Namen bleibt auf dem
>    System der Kanzlei und wird der Kanzlei übergeben.
> 4. **Geheimhaltung.** Die mit der Bestandsaufnahme betrauten Personen der Knovas GmbH sind als Hilfspersonen im
>    Sinne von Art. 13 BGFA und Art. 321 StGB zur Verschwiegenheit verpflichtet.
> 5. **Zeitraum.** Die Bestandsaufnahme findet am ___ statt. Das Konto wird danach gesperrt, sofern es nicht für
>    den Betrieb von Knovas weiterverwendet wird.
>
> Ort, Datum, Unterschrift Kanzlei: ___          Unterschrift Knovas GmbH: ___
