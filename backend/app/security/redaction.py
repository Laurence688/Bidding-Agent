import re


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {key: ('[REDACTED]' if key.lower() in {'api_key', 'authorization', 'token'} else redact(item, secrets)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return re.sub(r'(?i)Bearer\s+[^\s,;]+', 'Bearer [REDACTED]', value)
    return value
