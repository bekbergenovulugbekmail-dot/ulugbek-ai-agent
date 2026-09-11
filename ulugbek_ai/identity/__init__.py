"""Users. Kept deliberately small — authentication is a later phase, and this
module exists so every run, task and memory has a stable owner to hang off."""

from ulugbek_ai.identity.models import User
from ulugbek_ai.identity.repository import UserRepository

__all__ = ["User", "UserRepository"]
