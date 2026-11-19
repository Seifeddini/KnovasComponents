"""The Knovas Assistent: retrieve, compose, generate, check (PMVP-20).

``assistant.service`` holds the service and imports neither Flask nor the app
nor the HTTP client; ``assistant.core`` builds its two callables from the
existing ``KnovasAPIClient``. See README.md in this folder.

Kept empty on purpose: ``knovas_client`` imports ``assistant.errors``, so this
package must not import anything that imports the client back.
"""
