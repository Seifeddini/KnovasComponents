"""OneDrive → local-directory mirror, used as an RC watch root.

Runs on the host server (no browser, no user interaction) by authenticating to
Microsoft Graph with application client credentials. Files under the configured
drive/folder are downloaded into ``ONEDRIVE_MIRROR_PATH`` and refreshed on a
periodic background thread; RC's filesystem discover/sync treats that path as a
normal watch root.
"""
from onedrive_mirror.runner import start_mirror_thread_if_configured

__all__ = ["start_mirror_thread_if_configured"]
