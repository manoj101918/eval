"""Keep student data out of logs, whatever log level is configured.

Some libraries log payloads at DEBUG: the SQLite driver logs SQL statements with their
values (roll numbers, answers), and the Groq/Azure SDKs log request bodies (prompts with
student answers). Their loggers are capped so DEBUG output never reaches a handler.
"""

import logging

CAPPED = {
    "aiosqlite": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
    "groq": logging.WARNING,
    "azure": logging.WARNING,
    "httpcore": logging.WARNING,
    "httpx": logging.INFO,  # INFO lines carry only method, URL and status
    "multipart": logging.WARNING,
    "PIL": logging.WARNING,
}


def cap_library_loggers() -> None:
    for name, level in CAPPED.items():
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET or logger.level < level:
            logger.setLevel(level)
