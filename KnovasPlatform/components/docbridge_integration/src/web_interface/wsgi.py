"""
WSGI entrypoint for production web serving.

*Create the Flask application instance for WSGI servers*

@invariant The module-level `app` is a valid Flask WSGI application.
@requires PYTHONPATH includes `/app/src` when running inside container.
@ensures Importing this module does not start a development server.
@params None
@returns app: Flask - configured WSGI application object
@throws RuntimeError - if application creation fails during import
"""

from web_interface.app import create_app

app = create_app()
