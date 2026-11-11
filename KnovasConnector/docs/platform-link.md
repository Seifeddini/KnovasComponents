# Running the connector from the Platform

Under [connector protocol v1](../../KnovasPlatform/docs/connector-protocol-v1.md) the connector is set up with one line and run from the Platform. It calls out only: no port is opened, and nobody can start a transfer from outside.

## A new connector

1. **In the Platform:** Builder → *Verbinden* → *Komponente anmelden* → *Dokumente aus Dateiablage / DMS*. The last step shows the line, with a one-time token (24 hours).
2. **At the company:** replace `/PFAD/ZUR/ABLAGE` and run it. It makes its own key and enrols; its certificate and the Brain's address come from the enrolment.
3. **In the Platform:** create a source *Dateiablage / DMS* on it: the folder under `/sources`, and its access groups, or `alle` for the whole tenant. A folder without that choice is refused. Its page says whether it can read each source.
4. **Probelauf, then Jetzt anwenden.** A dry run counts what it would send; an apply sends exactly that list, once.

## An installation that already runs

Add `RC_PLATFORM_URL` and `RC_ENROL_TOKEN` to its settings and restart. It keeps its own certificate, Brain address and saved folder list, with their access groups.

What changes: it no longer starts sending by itself (`RC_SYNC_AUTO_START_CONTINUOUS` is switched off). Its schedule in the Platform runs dry runs only, and a person applies. The inbound routes keep working if `KNOVAS_INTERNAL_API_URL` and `RC_INSTANCE_TOKEN` stay set, so the Ingestion tab still reaches it; a person can then still start a continuous sync from there, without a dry run. To rule that out, unset those two and publish no port.

## What it does not do yet

- **Remove documents:** one removed from the share stays in the Brain, because a dry run does not show removals yet.
- **Send continuously:** new documents arrive when a person applies.
- **Work with the Platform's Ingestion tab, folder browser or Microsoft 365 preview** when it has no inbound route: those call the connector, which a one-line connector does not accept. Its folders and access groups are set on the source instead.
- **Renew its certificate by itself:** it holds for 365 days; *Token erneuern* gives a new one. Put the new token in `RC_ENROL_TOKEN` and restart.

## Settings

The top of `src/platform_link/bootstrap.py`.
