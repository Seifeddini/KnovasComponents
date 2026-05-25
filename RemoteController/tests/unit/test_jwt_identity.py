import base64
import json

from auth.jwt_identity import employee_id_from_jwt_token, employee_id_from_jwt_payload


def _jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def test_employee_id_from_sub():
    emp = "11111111-1111-1111-1111-111111111111"
    assert employee_id_from_jwt_token(_jwt({"sub": emp})) == emp


def test_employee_id_from_operator_id_claim():
    emp = "22222222-2222-2222-2222-222222222222"
    assert employee_id_from_jwt_payload({"operator_id": emp}) == emp


def test_employee_id_from_id_claim():
    emp = "b4ab2b41-2604-4766-8289-fcdfe78a8c2d"
    assert employee_id_from_jwt_payload({"id": emp, "email": "user@example.com"}) == emp


def test_rejects_non_uuid_claims():
    assert employee_id_from_jwt_token(_jwt({"sub": "not-a-uuid"})) is None


def test_rejects_malformed_token():
    assert employee_id_from_jwt_token("not-a-jwt") is None
