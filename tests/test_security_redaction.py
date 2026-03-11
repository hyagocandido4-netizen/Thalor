from __future__ import annotations

import json

from natbin.security.redaction import REDACTED, REDACTED_EMAIL, collect_sensitive_values, sanitize_payload


def test_sanitize_payload_redacts_nested_json_and_sensitive_keys() -> None:
    payload = {
        'broker': {
            'email': 'trader@example.com',
            'password': 'super-secret-pass',
            'balance_mode': 'PRACTICE',
        },
        'request_json': json.dumps(
            {
                'headers': {'authorization': 'Bearer super-secret-pass'},
                'user_email': 'trader@example.com',
                'safe': 1,
            }
        ),
        'note': 'user=trader@example.com token=super-secret-pass',
    }

    secrets = collect_sensitive_values(payload)
    clean = sanitize_payload(payload, sensitive_values=secrets, redact_email=True)

    assert clean['broker']['email'] == REDACTED_EMAIL
    assert clean['broker']['password'] == REDACTED
    assert REDACTED_EMAIL in clean['note']
    assert REDACTED in clean['note']

    request = json.loads(clean['request_json'])
    assert request['headers']['authorization'] == REDACTED
    assert request['user_email'] == REDACTED_EMAIL
    assert request['safe'] == 1
