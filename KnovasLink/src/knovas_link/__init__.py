"""Knovas Link: the one component per site that talks to Knovas (connector protocol 1.1, KC-7a).

The other components on the site talk only to Link, through its local API (contracts/local-api.md). Link holds the
site's key and certificate, runs the control plane with the Platform, relays the Brain calls on an allow-list, and
carries the work channel's jobs between secure-api and the components.
"""

VERSION = "1.1.0"
#: The kind Link enrols as with the Platform.
KIND = "knovas_link"
#: What Link declares in its enrolment and heartbeat.
CAPABILITIES = ["site", "work_channel", "brain_relay", "probes"]
