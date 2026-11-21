# Agenten: externe Agenten wie Claude Code mit den Rechten ihrer Rollen

Für Administratorinnen und Administratoren einer Plattform-Installation. Stand 21. November 2026 (RB-7 des Plans
FEAT-2026-11-19-platform-mvp; Entwurf `docs/platform-mvp/design/role-builder.md` § 4 und § 5).

## Was ein Agent ist

Ein Agent ist ein Mitglied von Rollen, das keine Person ist:

- **der Knovas Assistent**: Er arbeitet in der Plattform und handelt immer für die angemeldete Person. Er gehört zu
  jeder Rolle ausser der Administration, auch zu später angelegten. Auf der Seite einer Rolle nehmen Sie ihn heraus,
  wenn diese Rolle nicht von KI genutzt werden darf (zum Beispiel «Personal»).
- **ein externer Agent**, etwa Claude Code oder Copilot: Sie registrieren ihn unter Verwaltung → Agenten. Er liest über
  das MCP-Gateway von Knovas (`https://mcp.knovas.ch/mcp`), nie über die Plattform.

Ein Agent liest, was seine Rollen erlauben. Handelt er für eine Person, liest er nur, was auch diese Person lesen darf.
Ein Agent gehört nie zur Administration, und die direkten Zugriffsgruppen einer Person erreichen keinen Agenten: Daten
bekommt ein Agent nur über Rollen.

## Einen externen Agenten registrieren

1. Verwaltung → **Agenten** → «Agent registrieren».
2. Einen **Namen** geben (1 bis 80 Zeichen, eindeutig), zum Beispiel «Claude Code Sekretariat».
3. Wählen, **für wen** er handelt:
   - eine aktive Person: Er liest nur, was auch sie lesen darf, und hat keine Rechte mehr, sobald sie deaktiviert ist.
     Die Person lässt sich später nicht ändern; registrieren Sie dann einen neuen Agenten;
   - niemanden («selbständig»): Er liest mit den Rechten seiner Rollen. Ein solcher Agent hat höchstens 32
     Zugriffsgruppen; eine Rolle, die eine 33. brächte, wird abgelehnt.
4. Die Seite zeigt danach die **Kennung** des Agenten (eine UUID). Ein neuer Agent hat noch keine Rolle.
5. Auf der Seite einer Rolle unter «Mitglieder» → «+ Agent hinzufügen» geben Sie ihm Rollen.

## Den Schlüssel für das Gateway bei Knovas anfordern

Die Plattform kennt keinen Schlüssel eines Agenten und speichert keinen. Den Schlüssel stellt Knovas aus, gebunden an
die Kennung des Agenten:

- Schicken Sie Knovas die **Kennung** des Agenten (nicht seinen Namen) und den Namen Ihrer Installation.
- Knovas stellt den Schlüssel nur für eine Installation aus, bei der die Rechte durchgesetzt werden («Ein»).
- Sie erhalten den Schlüssel einmal (`knovas_gw_…`). Bewahren Sie ihn wie ein Passwort auf; die Plattform zeigt ihn
  nie, weil sie ihn nie sieht.

## Claude Code verbinden

```sh
claude mcp add --transport http knovas https://mcp.knovas.ch/mcp --header "Authorization: Bearer <Schlüssel>"
```

Das Gateway bietet die Werkzeuge `search` und `fetch`. Jeder Aufruf liest mit einem von der Plattform signierten
Nachweis der Rechte dieses Agenten.

## Wenn der Agent nichts liest: die zwei Antworten des Gateways

| Antwort | Bedeutet | Was tun |
|---|---|---|
| **«Knovas is busy, try again in a few seconds.»** | Die Rechte der letzten Minuten sind aufgebraucht. Ein Agent hat höchstens **20 Aufrufe pro Minute**: Die Plattform gibt Knovas jede Minute 20 signierte Nachweise, und jeder Aufruf verbraucht einen. | Einige Sekunden warten. |
| **«No current rights for this agent; nothing was read.»** | Knovas hat keine Rechte für diesen Agenten: Die Übergabe ist ausgeschaltet, der Agent ist widerrufen, seine Person ist nicht aktiv, er hätte mehr als 32 Zugriffsgruppen, oder die Plattform hat Knovas seit mehr als fünf Minuten nicht erreicht. | Unter Verwaltung → Agenten den Agenten und die Zeile «Übergabe an das Knovas-Gateway» prüfen; sonst Knovas fragen. |

## Die Übergabe an Knovas: der Schalter

Einstellungen → «Agenten über das Knovas-Gateway» (`AGENTS_CORE_GATEWAY` in der Installation setzt ihn fest), **aus**
als Standard. Eingeschaltet gibt die Plattform Knovas jede Minute, und sofort nach einer Änderung auf den Seiten der
Agenten, für jeden externen Agenten 20 signierte Nachweise seiner Rechte (je höchstens 300 Sekunden gültig). Für einen
widerrufenen Agenten, einen, dessen Person nicht aktiv ist, und einen mit mehr als 32 Zugriffsgruppen lässt sie Knovas
die Nachweise sofort verwerfen. Der Knovas Assistent wird nie übergeben: Er arbeitet in der Plattform.

Schalten Sie die Übergabe erst ein, wenn Knovas sie unterstützt und die Rechte Ihrer Installation durchgesetzt werden
(«Ein»). Ausgeschaltet erreicht Knovas nichts, und externe Agenten lesen über das Gateway nichts.

Eine Änderung an Rollen oder Personen auf anderen Seiten (Rollen, Personen) erreicht Knovas mit der nächsten Minute;
die Nachweise eines Agenten enden in jedem Fall nach höchstens 300 Sekunden.

## Einen Agenten widerrufen

Verwaltung → Agenten → «Widerrufen» beim Agenten. Er hat ab sofort keine Rechte mehr, und bei eingeschalteter Übergabe
lässt die Plattform Knovas seine Nachweise in derselben Anfrage verwerfen. Seine Rollen bleiben als Nachweis stehen.
Bitten Sie Knovas, den Schlüssel des Agenten ebenfalls zu sperren. Den Knovas Assistenten widerrufen Sie nicht; Sie
schalten ihn in den Einstellungen aus.

## Was protokolliert wird

Die Plattform schreibt jede Änderung (angelegt, widerrufen, einer Rolle hinzugefügt oder aus ihr genommen) mit Kennungen
ins Protokoll, nie mit Namen. In den Logs stehen Kennungen, Zahlen und Fehlerklassen, nie ein Nachweis, ein Name oder
eine Frage.
