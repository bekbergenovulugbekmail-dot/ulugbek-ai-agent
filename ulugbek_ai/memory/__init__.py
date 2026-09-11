"""Long-term memory.

Memory is *not* chat history. Each row is a typed, scoped, individually
retrievable fact, so a request can load only what is relevant instead of
replaying an entire conversation into the model's context.
"""

from ulugbek_ai.memory.manager import MemoryManager
from ulugbek_ai.memory.models import Memory
from ulugbek_ai.memory.repository import MemoryRepository
from ulugbek_ai.memory.search import KeywordSearchStrategy, MemorySearchStrategy

__all__ = [
    "KeywordSearchStrategy",
    "Memory",
    "MemoryManager",
    "MemoryRepository",
    "MemorySearchStrategy",
]
