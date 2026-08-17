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

    def _redact_value(self, value: object) -> object:
        """Strings are cleaned; everything else keeps its type so `%d` and friends still work."""
        return self._redact(value) if isinstance(value, str) else value

    def filter(self, record: logging.LogRecord) -> bool:
        # Message and arguments are redacted in place, never collapsed into one string.
        # Uvicorn's access formatter reads the five positional arguments off the record
        # instead of the rendered message, so replacing `args` with an empty tuple made every
        # access line raise ValueError and print a logging-error dump in its place.
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact_value(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._redact_value(value) for key, value in record.args.items()}
        if record.exc_info:
            exception_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = self._redact(exception_text)
            record.exc_info = None
        elif record.exc_text:
            # Another instance already rendered the traceback and cleared exc_info. Redacting
            # what it left behind is what makes two redactors compose: the process-wide one
            # knows the configured keys, a handler-local one may know others.
            record.exc_text = self._redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True


_BASE_RECORD_FACTORY = logging.getLogRecordFactory()


def configure_secret_redaction(settings_secrets: Iterable[str]) -> None:
    """Redact secrets from every log record in the process, whoever emits it.

    This deliberately does not attach the filter to a logger. A filter on the root logger is
    only consulted for records logged directly to root: records that a child logger emits are
    passed to ancestor *handlers*, never to ancestor filters. Uvicorn gives `uvicorn.error`
    and `uvicorn.access` their own handlers with `propagate = False` and leaves root without
    any, so a root-level filter sees nothing Uvicorn writes — including the access line for a
    webhook callback, which carries the shared secret as a query token.

    Replacing the record factory catches every logger, every handler, and any handler added
    after startup, which is the only shape that holds under `--reload` as well.
    """
    redaction_filter = SecretRedactionFilter(settings_secrets)

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        # Chained off the factory captured at import, never off the current one, so repeated
        # configuration replaces the redactor instead of stacking copies of it.
        record = _BASE_RECORD_FACTORY(*args, **kwargs)
        redaction_filter.filter(record)
        return record

    logging.setLogRecordFactory(factory)


def reset_secret_redaction() -> None:
    """Restore unredacted logging. Exists so tests can prove the redaction is what redacts."""
    logging.setLogRecordFactory(_BASE_RECORD_FACTORY)
