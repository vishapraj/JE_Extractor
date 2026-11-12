"""
The interface every model provider implements. The validator only talks to it,
so it never imports a vendor's SDK or depends on a cloud.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Provider(ABC):
    default_model: str
    default_escalation_model: Optional[str] = None

    def escalation_default(self, model: str) -> Optional[str]:
        """The second reviewer when LLM_ESCALATION_MODEL isn't set; None for no second review."""
        return self.default_escalation_model

    @abstractmethod
    def prepare(self, models: List[str]) -> None:
        """Called once, before the first review, with the models that may be used: connect, and raise
        ValueError now if one of them can't be used, rather than after a long first review."""

    @abstractmethod
    def complete(self, model: str, system: str, prompt: str, schema: Type[T]) -> Tuple[T, str]:
        """(a reply that matches schema, the model that gave it). Raises ValueError, with a message for the
        reviewer, whenever the model can't give one - the vendor's own errors included."""
