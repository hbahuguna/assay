from src.app import greeting, health


def test_health_shape():
    payload = health()
    assert payload["status"] == "ok"
    assert payload["service"] == "sample-service"


def test_greeting_handles_blank_input():
    assert greeting("   ") == "Hello, customer!"


def test_greeting_truncates_long_input():
    assert greeting("x" * 100).endswith("x" * 40 + "!")
