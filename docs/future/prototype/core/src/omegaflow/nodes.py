"""Node definitions and Structured Config schemas for OmegaFlow."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional
from omegaconf import DictConfig


class BaseNode(ABC):
    """Abstract base class for all OmegaFlow nodes."""
    
    def __init__(self, config: DictConfig):
        self.config = config
        self.id = config.get("id", "")
        self.next_node = config.get("next_node", None)
    
    @abstractmethod
    def execute(self, state: DictConfig) -> Dict[str, Any]:
        """Execute the node logic and return state updates."""
        pass


@dataclass
class InfoNodeConfig:
    """Structured config for InfoNode."""
    id: str
    message: str
    next_node: Optional[str] = None


class InfoNode(BaseNode):
    """Node that displays information."""
    
    def execute(self, state: DictConfig) -> Dict[str, Any]:
        """Execute info node - just returns the message."""
        return {"last_message": self.config.message}


@dataclass
class ProcessNodeConfig:
    """Structured config for ProcessNode."""
    id: str
    operation: str
    value: float
    next_node: Optional[str] = None


class ProcessNode(BaseNode):
    """Node that performs mathematical operations."""
    
    def execute(self, state: DictConfig) -> Dict[str, Any]:
        """Execute process node - performs operation on value."""
        current_value = state.get("current_value", 0.0)
        
        if self.config.operation == "add":
            result = current_value + self.config.value
        elif self.config.operation == "multiply":
            result = current_value * self.config.value
        elif self.config.operation == "set":
            result = self.config.value
        else:
            raise ValueError(f"Unknown operation: {self.config.operation}")
        
        return {"current_value": result}
