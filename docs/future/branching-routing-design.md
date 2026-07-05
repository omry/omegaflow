# OmegaFlow Branching and Routing Design (Class-Centric)

## Status
- Draft v2 for review before implementation
- Date: 2026-03-05

## Context
We want routing to be owned by node classes, not by an engine-level ownership switch.

The desired model:
1. Every node has a `transition()` function.
2. `transition()` chooses the next node from that node's configured outgoing nodes.
3. `DecisionNode` is a reusable node type that implements config-driven branching logic.
4. Any node can route to a `DecisionNode` as its single next hop to reuse branching logic without duplicating rules.

## Goals
1. Keep routing behavior encapsulated in node classes.
2. Support both custom logic nodes and declarative rule-based routing.
3. Keep engine simple: execute, then ask node where to go.
4. Ensure safety: next node must be declared and validated.

## Non-Goals
1. Parallel execution.
2. Global transition DSL for all node types.
3. Cycle detection policy (can be added separately).

## Core Model

### Root config structure
Runtime data is top-level in the OmegaConf root and is split into:
- `runtime`: engine-owned runtime metadata
- `session`: values collected/produced while running the graph

Proposed shape:

```yaml
runtime:
  current_node: null
  step_count: 0
  started_at: null

# example of variables collected during traversal of the flow
session:
  vm_ip: 192.168.56.101
  mysql:
    user: "root"
    password: "1234"

omegaflow:
  entrypoint: start
  nodes: {}
```

Path conventions:
- `DecisionNode.when.path` supports either:
  - dotted lookup path from root (example: `session.vm_ip`)
  - OmegaConf interpolation expression (example: `${session.os}`)
- Interpolations from node config can reference root keys directly (example: `${session.vm_ip}`).
- Recommended usage:
  - use interpolation in `when.path` for value comparisons (`eq`, `ne`, `gt`, ...)
  - use dotted paths for `exists`

### Base node contract
All nodes implement:
- `execute(session) -> NodeResult`
- `transition(context, result: NodeResult) -> str | None`

`transition()` returns:
- `node_id` to continue flow
- `None` to terminate flow

Proposed base class:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal
from omegaconf import DictConfig


@dataclass
class NodeResult:
    status: Literal["success", "error"] = "success"
    code: str | None = None
    message: str | None = None


class BaseNode(ABC):
    def __init__(self, config: DictConfig):
        self.config = config
        self.id: str = config.get("id", "")
        self.outgoing: list[str] = list(config.get("outgoing", []))

    @abstractmethod
    def execute(self, session: DictConfig) -> NodeResult:
        """Run node logic, mutate session in place, return status/result."""

    def transition(self, context: DictConfig, result: NodeResult) -> str | None:
        """Choose next node id from `self.outgoing`, or None to terminate."""
        return None
```

### NodeResult
`execute()` returns the `NodeResult` type defined in the base class above.

Notes:
- Routing is not returned from `execute()` in this model.
- Routing is decided by `transition()` only.
- If `status == "error"`, node should set `code` and `message`.
- `code` is an open string namespace (no central enum required).
- `transition()` receives `result` directly and can choose recovery or termination.
- Nodes mutate `session` directly; no `updates` patch merge step is used.

### Outgoing edges
Each node config declares allowed next nodes:

```yaml
omegaflow:
  nodes:
    some_node:
      _target_: tutorial.nodes.SomeNode
      outgoing: [node_a, node_b]
```

Rule:
- `transition()` output must be one of `outgoing` (or `None`).

## DecisionNode

### Purpose
`DecisionNode` is a reusable router node that chooses next node using config rules and current runtime/session/config values.

### Behavior
- `execute()` typically does nothing (or optional lightweight bookkeeping).
- `transition()` evaluates configured rules in order.
- First match wins; else default; else `None`.

### DecisionNode config shape

```yaml
omegaflow:
  nodes:
    os_router:
      _target_: omegaflow.nodes.DecisionNode
      outgoing: [windows_path, linux_path, macos_path, unsupported_path]
      rules:
        - when: {path: "${session.os}", op: "eq", value: "windows"}
          to: windows_path
        - when: {path: "${session.os}", op: "eq", value: "linux"}
          to: linux_path
        - when: {path: "${session.os}", op: "eq", value: "macos"}
          to: macos_path
        - default: unsupported_path
```

`rules` DSL (initial):
- Conditional rule form:
  - `when` (required)
  - `to` (required)
- Default rule form:
  - `default` (required, target node id)

`when` predicate:
- `path`: dotted lookup path or interpolation expression
- `op`: `eq | ne | gt | gte | lt | lte | in | not_in | exists`
- `value`: required except for `exists` (supports OmegaConf interpolation)
- evaluation:
  - if `path` is dotted (for example `session.os`), evaluator reads that key from context
  - if `path` is interpolation (for example `${session.os}`), OmegaConf resolves it first
  - unresolved interpolation is treated as rule evaluation error (fails fast)
- operator policy:
  - `exists` requires dotted `path` (example: `session.os`)
  - interpolation `path` is not allowed with `exists`
- optional advanced usage:
  - `oc.select` can be used when interpolation with default behavior is needed
    (example: `${oc.select:session.os,unknown}`)

## Reuse Pattern (Attach DecisionNode)
Use a DecisionNode as the single next hop for any action node:

```yaml
omegaflow:
  nodes:
    collect_os:
      _target_: tutorial.nodes.CollectOSNode
      outgoing: [os_router]

    os_router:
      _target_: omegaflow.nodes.DecisionNode
      outgoing: [windows_path, linux_path, unsupported_path]
      rules:
        - when: {path: "${session.os}", op: "eq", value: "windows"}
          to: windows_path
        - when: {path: "${session.os}", op: "eq", value: "linux"}
          to: linux_path
        - default: unsupported_error

    unsupported_error:
      _target_: omegaflow.nodes.ErrorNode
      message: "Unsupported OS: ${session.os}"
      outgoing: []
```

Benefits:
- business/action nodes stay focused on producing session/runtime updates
- routing logic is centralized and reusable
- no repeated branching logic in many classes

## Engine Runtime Flow
For each step:
1. Update engine runtime metadata:
   - `runtime.current_node = <current_node_id>`
   - `runtime.step_count += 1`
2. Execute current node: `result = node.execute(session)`.
   - `session` is mutable and updated in place by node logic
3. Validate result contract:
   - `status` is `success` or `error`
   - if `status == "error"`, require `code` and `message`
4. Ask node for next: `next_node = node.transition(context, result)`.
   - this runs for both success and error results
   - node may route to a recovery node on error
5. Validate `next_node`:
   - `None` => stop
   - must exist in current node's `outgoing`
6. Move to `next_node`.

Engine does not evaluate transition DSL directly; `DecisionNode` does.

## ErrorNode
`ErrorNode` is a terminal utility node for explicit failure paths.

Behavior:
- `execute()` prints/emits the configured error message.
- `execute()` returns `NodeResult(status="error", code=..., message=...)`.
- `execute()` may also write `error` details directly into `session`.
- `transition()` always returns `None` (terminate).

Config shape:

```yaml
omegaflow:
  nodes:
    unsupported_error:
      _target_: omegaflow.nodes.ErrorNode
      message: "Unsupported OS: ${session.os}"
      outgoing: []
```

## Validation
Startup validation:
1. Every node id in `outgoing` exists in graph.
2. `DecisionNode` rules:
   - conditional rules must include both `when` and `to`
   - default rule is `default: <node_id>`
   - conditional `to` and default target are in node `outgoing`
   - at most one default rule
   - valid `when` schema and operator
3. Non-Decision nodes may still have custom config, but no global routing DSL validation is applied.

Runtime validation:
1. If a node returns unknown next node -> `ValueError` with node id and returned value.
2. If DecisionNode interpolation or predicate eval fails unexpectedly -> `ValueError` with rule index.
3. If NodeResult status/code/message contract is invalid -> `ValueError` with node id.
4. If `exists` is used with interpolation `path` -> `ValueError` with rule index.

## Node API Changes
1. Add `transition(self, context, result: NodeResult) -> str | None` to `BaseNode`.
2. Remove config field `next_node` from base model.
3. Add new `DecisionNode` class implementing rules DSL.
4. Add new `ErrorNode` class for terminal failure paths.
5. Update existing built-in nodes (`InfoNode`, `ProcessNode`):
   - default `transition()` behavior should be explicit (see open questions below).

## Testing Plan
1. Node custom transition returns valid outgoing node.
2. Node custom transition returns invalid node -> runtime error.
3. DecisionNode rule match routes correctly.
4. DecisionNode default route works.
5. DecisionNode with no match and no default terminates.
6. DecisionNode invalid config fails at startup.
7. Attach-DecisionNode pattern end-to-end (action node -> DecisionNode -> branch target).
8. ErrorNode emits message and terminates (`transition() -> None`).
9. Error result can route to recovery node when node `transition()` chooses one.

## Implementation Phases
1. Introduce `BaseNode.transition(context, result)` and engine call path.
2. Add `outgoing` validation for all nodes.
3. Implement `DecisionNode` with rule DSL evaluator.
4. Implement `ErrorNode`.
5. Migrate built-in nodes to explicit transition behavior.
6. Add tests for DecisionNode + composition pattern.

## Open Questions
1. For non-Decision nodes, what should default `transition()` do?
   - Option A: return first outgoing if exactly one, else error
   - Option B: always return `None` unless subclass overrides
2. Should `outgoing` be required for all nodes, including terminal nodes?
3. Should DecisionNode require a default rule, or allow unmatched termination?
