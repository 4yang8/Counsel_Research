"""Shared cooperative cancellation, independent of optional tool transports."""


class Cancelled(Exception):
    pass
