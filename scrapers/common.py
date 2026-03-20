from __future__ import annotations


class PlatformFetchError(Exception):
    def __init__(self, platform: str, message: str):
        super().__init__(message)
        self.platform = platform
        self.message = message

