from typing import TYPE_CHECKING

from memory.api.server import create_app

if TYPE_CHECKING:
    from memory.api.asgi import app

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> object:
    if name == "app":
        from memory.api.asgi import app

        return app
    raise AttributeError(name)
