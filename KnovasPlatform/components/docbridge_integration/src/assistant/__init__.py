"""The Knovas Assistent: retrieve, compose, generate, check (PMVP-20).

``assistant.service`` holds the service and imports neither Flask nor the app
nor the HTTP client; ``assistant.check`` checks the model's statements (form,
source, quote, numbers, length); ``assistant.core`` builds the service's two
callables from the existing ``KnovasAPIClient``. See README.md in this folder.

Kept empty on purpose: ``knovas_client`` imports ``assistant.errors``, so this
package must not import anything that imports the client back.
"""
