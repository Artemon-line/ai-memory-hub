from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.api.asgi import app
    from memory.api.server import create_app

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> object:
    if name == "app":
        from memory.api.asgi import app

        return app
    if name == "create_app":
        from memory.api.server import create_app

        return create_app
    raise AttributeError(name)
