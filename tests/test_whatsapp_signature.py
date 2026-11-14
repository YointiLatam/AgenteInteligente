from app.whatsapp.signature import sign, verify_signature

SECRET = "app-secret"
BODY = b'{"object":"whatsapp_business_account"}'


def test_valid_signature():
    assert verify_signature(SECRET, BODY, sign(SECRET, BODY))


def test_wrong_secret_or_tampered_body():
    assert not verify_signature(SECRET, BODY, sign("other", BODY))
    assert not verify_signature(SECRET, BODY + b" ", sign(SECRET, BODY))


def test_missing_or_malformed_header():
    assert not verify_signature(SECRET, BODY, None)
    assert not verify_signature(SECRET, BODY, "")
    assert not verify_signature(SECRET, BODY, sign(SECRET, BODY).removeprefix("sha256="))
    assert not verify_signature(SECRET, BODY, "sha1=abc")
