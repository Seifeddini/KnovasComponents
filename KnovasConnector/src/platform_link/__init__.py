"""The Knovas Connector under connector protocol v1: it enrols itself and is run from the Platform, calling out only.

Switched on by ``RC_PLATFORM_URL`` (the Platform's protocol address) with a one-time ``RC_ENROL_TOKEN``. Then one line
starts a connector: it makes its own key, enrols, takes its certificate and the Brain's address from the enrolment, and
needs no inbound port. It writes to the Brain only when a person gave the command after a dry run, and only the
documents that dry run counted. A schedule only runs dry runs. Everything it reports is codes and counts: no document
name and no path reaches the Platform (GI-DATA-02/04).

Without ``RC_PLATFORM_URL`` nothing here runs and the connector behaves as it always has.

The contract is KnovasPlatform/docs/connector-protocol-v1.md. ``client``, ``identity`` and ``agent`` import nothing of
the connector, so they can be tested against the Platform's real protocol; ``operations`` is the connector's side.
"""
