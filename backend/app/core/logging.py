import logging
import re
from collections.abc import Iterable

_AUTHORIZATION_PATTERN = re.compile(r"(?i)Authorization\s*:\s*Bearer\s+\S+")


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def _redact(self, value: str) -> str:
        value = _AUTHORIZATION_PATTERN.sub("Authorization: [REDACTED]", value)
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            exception_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = self._redact(exception_text)
            record.exc_info = None
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True


def configure_secret_redaction(settings_secrets: Iterable[str]) -> None:
    redaction_filter = SecretRedactionFilter(settings_secrets)
    root_logger = logging.getLogger()
    root_logger.addFilter(redaction_filter)
    for handler in root_logger.handlers:
        handler.addFilter(redaction_filter)
