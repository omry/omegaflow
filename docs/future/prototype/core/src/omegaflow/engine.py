"""OmegaFlow execution engine."""

import importlib
from typing import Any, Dict, Optional, Type
from omegaconf import DictConfig, OmegaConf, ValidationError

from .nodes import BaseNode


class OmegaFlowEngine:
    """Interactive graph-based execution engine."""
    
    def __init__(self, config: DictConfig):
        """Initialize the engine with a configuration.
        
        Args:
            config: OmegaConf configuration containing nodes and state
        """
        self.config = config
        self.nodes: Dict[str, BaseNode] = {}
        self.state = config.get("state", OmegaConf.create({}))
        self.entrypoint = config.get("entrypoint", "")
        
        # Instantiate all nodes
        self._instantiate_nodes()
    
    def _instantiate_nodes(self) -> None:
        """Instantiate all nodes from the configuration."""
        nodes_config = self.config.get("nodes", {})
        
        for node_id, node_config in nodes_config.items():
            if "_target_" not in node_config:
                raise ValueError(f"Node {node_id} missing _target_ key")
            
            # Add the node ID to the config
            node_config_with_id = OmegaConf.merge(node_config, {"id": node_id})
            
            # Instantiate the node
            node = self._instantiate_node(node_config_with_id)
            self.nodes[node_id] = node
    
    def _instantiate_node(self, node_config: DictConfig) -> BaseNode:
        """Instantiate a single node from its configuration.
        
        Args:
            node_config: Configuration for the node including _target_
            
        Returns:
            Instantiated node instance
            
        Raises:
            ValidationError: If the config doesn't match the structured config
            ValueError: If the target class cannot be found or instantiated
        """
        target = node_config._target_
        
        # Parse the target string to get module and class
        if "." not in target:
            raise ValueError(f"Invalid target format: {target}")
        
        module_path, class_name = target.rsplit(".", 1)
        
        try:
            # Import the module and get the class
            module = importlib.import_module(module_path)
            node_class = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise ValueError(f"Cannot import {target}: {e}")
        
        
        # Instantiate the node
        if not issubclass(node_class, BaseNode):
            raise ValueError(f"Class {target} is not a subclass of BaseNode")
        
        return node_class(node_config)
    
    def execute_node(self, node_id: str) -> Dict[str, Any]:
        """Execute a specific node and update the state.
        
        Args:
            node_id: ID of the node to execute
            
        Returns:
            State updates from the node execution
            
        Raises:
            ValueError: If the node ID is not found
        """
        if node_id not in self.nodes:
            raise ValueError(f"Node {node_id} not found")
        
        node = self.nodes[node_id]
        updates = node.execute(self.state)
        
        # Update the state with the results
        self.state = OmegaConf.merge(self.state, updates)
        
        return updates
    
    def get_next_node(self, current_node_id: str) -> Optional[str]:
        """Get the next node ID from the current node.
        
        Args:
            current_node_id: ID of the current node
            
        Returns:
            Next node ID or None if no next node
        """
        if current_node_id not in self.nodes:
            return None
        
        return self.nodes[current_node_id].next_node
    
    def run_from_entrypoint(self) -> Dict[str, Any]:
        """Run the graph starting from the entrypoint.
        
        Returns:
            Final state after execution
        """
        if not self.entrypoint:
            raise ValueError("No entrypoint specified")
        
        current_node_id = self.entrypoint
        
        while current_node_id:
            self.execute_node(current_node_id)
            current_node_id = self.get_next_node(current_node_id)
        
        return OmegaConf.to_container(self.state, resolve=True)
