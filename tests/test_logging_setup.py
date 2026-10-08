import logging

from backend.logging_setup import CAPPED, cap_library_loggers


def test_library_loggers_never_emit_debug():
    logging.getLogger("aiosqlite").setLevel(logging.NOTSET)
    cap_library_loggers()
    for name, level in CAPPED.items():
        logger = logging.getLogger(name)
        assert not logger.isEnabledFor(logging.DEBUG), name
        assert logger.level >= level
