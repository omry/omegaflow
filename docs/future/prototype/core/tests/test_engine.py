"""Tests for OmegaFlow engine."""

import pytest
from omegaconf import OmegaConf, ValidationError

from omegaflow.engine import OmegaFlowEngine
from omegaflow.nodes import InfoNode, ProcessNode


class TestOmegaFlowEngine:
    """Test cases for OmegaFlowEngine."""
    
    def test_dynamic_binding_info_node(self):
        """Test that _target_ correctly instantiates InfoNode."""
        config = OmegaConf.create({
            "nodes": {
                "info1": {
                    "_target_": "omegaflow.nodes.InfoNode",
                    "message": "Hello World"
                }
            },
            "entrypoint": "info1"
        })
        
        engine = OmegaFlowEngine(config)
        
        # Verify the correct class was instantiated
        assert "info1" in engine.nodes
        assert isinstance(engine.nodes["info1"], InfoNode)
        assert engine.nodes["info1"].config.message == "Hello World"
    
    def test_dynamic_binding_process_node(self):
        """Test that _target_ correctly instantiates ProcessNode."""
        config = OmegaConf.create({
            "nodes": {
                "proc1": {
                    "_target_": "omegaflow.nodes.ProcessNode",
                    "operation": "add",
                    "value": 5.0
                }
            },
            "entrypoint": "proc1"
        })
        
        engine = OmegaFlowEngine(config)
        
        # Verify the correct class was instantiated
        assert "proc1" in engine.nodes
        assert isinstance(engine.nodes["proc1"], ProcessNode)
        assert engine.nodes["proc1"].config.operation == "add"
        assert engine.nodes["proc1"].config.value == 5.0
    
    def test_invalid_target_raises_error(self):
        """Test that invalid _target_ raises ValueError."""
        config = OmegaConf.create({
            "nodes": {
                "invalid1": {
                    "_target_": "nonexistent.module.NonexistentClass",
                    "message": "test"
                }
            },
            "entrypoint": "invalid1"
        })
        
        with pytest.raises(ValueError, match="Cannot import"):
            OmegaFlowEngine(config)
    
    def test_missing_target_raises_error(self):
        """Test that missing _target_ raises ValueError."""
        config = OmegaConf.create({
            "nodes": {
                "no_target": {
                    "message": "test"
                    # Missing _target_
                }
            },
            "entrypoint": "no_target"
        })
        
        with pytest.raises(ValueError, match="missing _target_ key"):
            OmegaFlowEngine(config)
    
    def test_pointer_logic_single_node(self):
        """Test execution starting from entrypoint with single node."""
        config = OmegaConf.create({
            "nodes": {
                "start": {
                    "_target_": "omegaflow.nodes.InfoNode",
                    "message": "Starting execution"
                }
            },
            "entrypoint": "start",
            "state": {}
        })
        
        engine = OmegaFlowEngine(config)
        result = engine.run_from_entrypoint()
        
        assert result["last_message"] == "Starting execution"
    
    def test_pointer_logic_multiple_nodes(self):
        """Test execution flow through multiple connected nodes."""
        config = OmegaConf.create({
            "nodes": {
                "start": {
                    "_target_": "omegaflow.nodes.ProcessNode",
                    "operation": "set",
                    "value": 10.0,
                    "next_node": "multiply"
                },
                "multiply": {
                    "_target_": "omegaflow.nodes.ProcessNode",
                    "operation": "multiply",
                    "value": 2.0,
                    "next_node": "info"
                },
                "info": {
                    "_target_": "omegaflow.nodes.InfoNode",
                    "message": "Calculation complete"
                }
            },
            "entrypoint": "start",
            "state": {"current_value": 0.0}
        })
        
        engine = OmegaFlowEngine(config)
        result = engine.run_from_entrypoint()
        
        assert result["current_value"] == 20.0
        assert result["last_message"] == "Calculation complete"
    
    def test_get_next_node(self):
        """Test getting next node ID from current node."""
        config = OmegaConf.create({
            "nodes": {
                "node1": {
                    "_target_": "omegaflow.nodes.InfoNode",
                    "message": "First",
                    "next_node": "node2"
                },
                "node2": {
                    "_target_": "omegaflow.nodes.InfoNode",
                    "message": "Second"
                }
            },
            "entrypoint": "node1"
        })
        
        engine = OmegaFlowEngine(config)
        
        assert engine.get_next_node("node1") == "node2"
        assert engine.get_next_node("node2") is None
        assert engine.get_next_node("nonexistent") is None
    
    def test_execute_single_node(self):
        """Test executing a single node."""
        config = OmegaConf.create({
            "nodes": {
                "proc1": {
                    "_target_": "omegaflow.nodes.ProcessNode",
                    "operation": "add",
                    "value": 15.0
                }
            },
            "entrypoint": "proc1",
            "state": {"current_value": 5.0}
        })
        
        engine = OmegaFlowEngine(config)
        updates = engine.execute_node("proc1")
        
        assert updates["current_value"] == 20.0
        assert engine.state.current_value == 20.0
    
    def test_execute_nonexistent_node_raises_error(self):
        """Test that executing nonexistent node raises ValueError."""
        config = OmegaConf.create({
            "nodes": {},
            "entrypoint": ""
        })
        
        engine = OmegaFlowEngine(config)
        
        with pytest.raises(ValueError, match="Node nonexistent not found"):
            engine.execute_node("nonexistent")
    
    def test_run_without_entrypoint_raises_error(self):
        """Test that running without entrypoint raises ValueError."""
        config = OmegaConf.create({
            "nodes": {
                "node1": {
                    "_target_": "omegaflow.nodes.InfoNode",
                    "message": "test"
                }
            }
            # No entrypoint specified
        })
        
        engine = OmegaFlowEngine(config)
        
        with pytest.raises(ValueError, match="No entrypoint specified"):
            engine.run_from_entrypoint()
