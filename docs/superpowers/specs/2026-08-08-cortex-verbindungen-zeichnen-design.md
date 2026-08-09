# Cortex: Verbindungen im Graphen zeichnen — Design

**Datum:** 2026-08-08 · **Betrifft:** Typ-zu-Typ und Entität-zu-Entität

## Zweck

Verbindungen entstehen heute über ein Formular im Drawer (Beziehungsname
eintippen, Zielentität aus einer Liste wählen). Das ist umständlich und
passt nicht dazu, dass daneben ein Graph steht. Ziel: Verbindungen
**direkt im Graphen ziehen** — auf beiden Ebenen.

## Ausgangslage (geprüft in der API-Spec)

Zwei Befunde bestimmen den Entwurf:

1. **Die Knovas-API kennt keine Verbindung zwischen Typen.** Kanten
   (`/secured/graph/edges`) gibt es nur zwischen Knoten. Was Cortex heute
   als Typ-Linie zeigt, ist eine Verdichtung: `summary()` zählt die
   Entitätskanten je Typ-Paar zusammen.
2. **Das passende Gegenstück ist das Schema.** Ein Knotentyp kann
   Attribute haben; der Datentyp `entity_ref` „materialisiert eine
   typisierte Kante". Ein Attribut „hat Dossier" vom Typ `entity_ref` auf
   dem Typ *Mandant* ist die ehrliche Übersetzung von „Mandanten haben
   Dossiers". Schemas sind laut Spec **Overlays**: sie machen Fehlendes
   sichtbar, sie verbieten nichts.

Daraus folgt: eine Typ-Linie kann zweierlei bedeuten, und beides ist
wertvoll. Der Entwurf trennt sie sichtbar.

## Entscheidungen

| Frage | Entscheid |
|---|---|
| Bedeutung einer Typ-Linie | Vorgabe und Beobachtung getrennt darstellen |
| Geste | Griff am Knoten, von dort auf das Ziel ziehen |
| Benennung | Immer nachfragen, mit Vorschlägen aus bereits verwendeten Namen |
| Vorgabe ohne Instanzen | Bleibt sichtbar (macht die Lücke sichtbar) |
| Griff erscheint bei | Typen **und** Entitäten |

## Zwei Bedeutungen, am Strich erkennbar

- **Durchgezogen mit Zahl** = *beobachtet*. Verdichtung echter
  Entitätsverbindungen, wie heute („enthält Vertrag (560)").
- **Gestrichelt ohne Zahl** = *Vorgabe*. Gezeichnet: „Mandanten haben
  Dossiers." Bleibt bestehen, solange keine Instanz existiert.

Entsteht die erste echte Verbindung dieser Art, wird die Linie
durchgezogen und bekommt einen Zähler. Das Modell füllt sich sichtbar.

**Kein neues Feld nötig:** Eine Typ-Relation mit `count: 0` ist eine
Vorgabe, alles darüber ist beobachtet — verdichtete Relationen haben
immer mindestens eine Instanz.

## Die Geste

1. Zeigen auf einen Knoten → kleiner Griff am Rand. Bei Satelliten
   erscheint er etwas grösser als der Knoten, damit er zu treffen ist.
2. Vom Griff auf einen anderen Knoten ziehen → eine Linie folgt dem
   Zeiger, gültige Ziele heben sich hervor.
3. Loslassen → schmales Feld für den Namen, mit Vorschlägen aus bereits
   verwendeten Bezeichnungen. Eingabetaste legt an, Escape verwirft.

**Die Ebene folgt dem Knotentyp:** Typ → Typ legt eine Vorgabe an,
Entität → Entität eine echte Verbindung. Gemischte Züge werden
abgewiesen (das Ziel hebt sich nicht hervor). Verschieben von Knoten
bleibt möglich, weil der Zug am Griff beginnt, nicht am Knoten.

## Datenwege

| Aktion | Fixture | Knovas API |
|---|---|---|
| Vorgabe anlegen | Eintrag in `relations`, `count: 0` | `POST /secured/graph/node-types/<id>/schema`, Attribut mit `datatype: entity_ref` |
| Verbindung anlegen | Eintrag in `entity_relations` | `POST /secured/graph/edges` mit `node_lo`, `node_hi`, `relation` |
| Vorgabe löschen | Eintrag entfernen | `DELETE …/node-types/<id>/schema/<aid>` |
| Verbindung löschen | Eintrag entfernen | `DELETE /secured/graph/edges/<id>` |

Neue Route `POST /api/ontology/type-relations` (Vorgaben); für
Entitätsverbindungen dient die bestehende `POST /api/ontology/relations`.
Löschen über `DELETE` auf beiden. Alle hinter Login und CSRF wie bisher.

**Offen bis zum ersten Lauf gegen eine echte Instanz:** Die Spec zeigt
den Body von `POST …/schema` nicht. Wir senden `name` und
`datatype: "entity_ref"` und lesen die Antwort tolerant.

## Löschen

Eine falsch gezogene Linie muss weg können. Klick auf eine Linie zeigt
sie im Drawer mit Namen, Richtung und Löschknopf — dasselbe
Bestätigungsblatt wie bei Typen, das die Folgen benennt.

## Fehlerbehandlung

- Zug ins Leere oder auf einen ungültigen Knoten: Linie verschwindet,
  nichts passiert, keine Meldung.
- Anlegen scheitert: die Linie wird nicht gezeichnet, Hinweis im Drawer.
  Der Graph zeigt nie eine Verbindung, die serverseitig nicht existiert.
- Doppelte Verbindung (gleiches Paar, gleicher Name): der Server gibt den
  bestehenden Eintrag zurück, im Graphen entsteht keine zweite Linie.

## Tests

1. Store: Vorgabe anlegen und löschen, Zählung bleibt korrekt
2. Store: doppelte Verbindung legt nicht doppelt an
3. Graph-Quelle: Vorgabe → Schema-Attribut, Verbindung → Kante
   (simulierter Client, beide Richtungen der Feldnamen)
4. Routen: Auth und CSRF für beide neuen Endpunkte
5. Verdichtung: Vorgabe mit `count: 0` bleibt neben beobachteten Linien
   erhalten

## Nicht enthalten (bewusst)

Zielkontrolle beim Zeichnen (Schemas verbieten laut Spec nichts) ·
Richtungsumkehr per Geste · Mehrfachauswahl · Verbindungen mit eigenen
Eigenschaften · Bootstrap aus Dateinamen (eigener Task, siehe unten).

## Zusammenhang: warum das Zeichnen nicht die Massenarbeit ist

DocBridge-Dateinamen tragen die Struktur bereits
(`GUID_AktenID_Dokumenttyp`), und Knovas sortiert neu eingehende
Dokumente über **Identifiers** automatisch den passenden Knoten zu. Die
Massenarbeit ist damit abgedeckt. Das Zeichnen dient dem **Modell** —
Beziehungen, die in keinen Metadaten stehen. Der Ableitungslauf über den
Bestand ist als eigener Task festgehalten und vor dem produktiven
Einsatz mit echten Daten nötig.
