"""Users. Kept deliberately small: the API authenticates one operator against a
shared token, so this module exists to give every run, task and memory a stable
owner to hang off, not to hold credentials."""

from ulugbek_ai.identity.models import User
from ulugbek_ai.identity.repository import UserRepository

__all__ = ["User", "UserRepository"]
