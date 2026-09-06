# v1.0.0

Customer deploy bundle for Knovas.

## KnovasPlatform

Docker search UI for an indexed Knovas tenant. Requires mTLS client certificates and company login configuration.

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
