# Task 5 Report: Reject browser-supplied access groups

## Status

Implemented an application-wide JSON request gate that rejects any
browser-supplied `access_groups` field with HTTP 400. The gate delegates to
`PrincipalBroker.reject_client_assertion` and handles
`ClientAssertedGroupsError`; it does not duplicate the identity-layer check.

## TDD RED

Created `tests/test_web_rejects_client_groups.py` with a module-local Flask test
client fixture. The fixture creates and signs in a real identity user and sends
the signed-in session's CSRF token on JSON POST requests.

The first run exposed a fixture-only error because the search page renders its
CSRF token in JavaScript rather than a hidden input. After correcting the
fixture to read the signed-in session token, the intended RED result was:

```text
tests/test_web_rejects_client_groups.py FF.
2 failed, 1 passed

access_groups=["litigation"]: expected 400, received 200
access_groups=[]: expected 400, received 200
normal request: received 200
```

## TDD GREEN

Added a `before_request` hook beside the existing CSRF gate in
`src/web_interface/app.py`. For JSON requests it passes the parsed body to
`PrincipalBroker.reject_client_assertion`; a `ClientAssertedGroupsError` becomes
the required HTTP 400 JSON response.

Focused GREEN:

```text
tests/test_web_rejects_client_groups.py ...  [100%]
3 passed, 1 warning in 1.66s
```

Regression verification:

```text
pytest tests/test_web_rejects_client_groups.py \
       tests/test_web_login_identity.py \
       tests/test_identity_principal.py \
       tests/test_knovas_client_assertion.py -v

37 passed, 1 warning in 11.70s
```

The warning is the pre-existing RequestsDependencyWarning for the installed
urllib3/chardet/charset_normalizer versions.
