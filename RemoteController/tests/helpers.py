import base64
import json

TEST_EMPLOYEE_ID = "11111111-1111-1111-1111-111111111111"


def make_test_jwt(
    employee_id: str = TEST_EMPLOYEE_ID,
    *,
    jti: str = "test-jti",
) -> str:
    header = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": employee_id, "jti": jti}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"
