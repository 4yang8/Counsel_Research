"""Compatibility hook: all versions now use the common batched event journal."""


def log_factory(base):
    return base
