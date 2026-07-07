# OmegaFlow Graph Playback Direction

## Status

Exploratory direction note. This is not an implementation commitment or a
rename plan.

## Context

The current OmegaFlow is a linear authoring and build surface for
terminal recordings. A recording script defines setup, narrated beats, commands,
expectations, generated audio, retiming, publish surfaces, and alignment checks.

There is a related older pre-incubation idea called OmegaFlow that explored a
general OmegaConf-backed workflow graph with nodes, transitions, decision
points, runtime state, and reusable subgraphs. Those ideas are useful seed
material, but the name is likely to be confused with OmegaConf and should not
drive the current OmegaFlow naming.

## Direction

OmegaFlow can evolve toward non-linear playback without requiring asciinema itself
to become non-linear.

The useful split is:

- asciinema remains the linear terminal media format
- OmegaFlow owns the graph, choices, contracts, narration, artifacts, and routing
- each playable segment is linear and has explicit start and end invariants
- junctions choose among segments whose preconditions are satisfied

In this model, a non-linear tutorial is a graph of validated linear segments.
The player pauses at choice or decision points, then loads or seeks to the next
segment selected by user input or graph rules.

## Segment Contracts

Each segment should be able to declare:

- preconditions: what must be true before playback or recording can start
- postconditions: what the segment guarantees after it completes
- visible start state: what the terminal should show at segment entry
- visible end state: what downstream segments can assume visually
- state updates: selected options, command results, generated files, service
  state, or other session data
- failure routes: where to go when a check fails

The key validity rule is:

> An edge is valid when the upstream segment's postconditions satisfy the
> downstream segment's preconditions.

OmegaFlow already has the beginning of this model in recording `expect` checks.
The next step is to make those checks part of a first-class segment contract and
add matching preconditions.

## Example Shape

```yaml
segments:
  common_setup:
    cast: setup.cast
    ensures:
      files:
      - ./arbiterctl
      session:
        deployment: staged
    next: choose_install_mode

  choose_install_mode:
    type: choice
    prompt: Choose an install path
    options:
      docker: docker_install
      local: local_install

  docker_install:
    cast: docker-install.cast
    requires:
      session:
        deployment: staged
      files:
      - ./arbiterctl
    ensures:
      service:
        arbiter: running
```

## Practical Notes

Branch segments should start from a known terminal and environment state. The
simplest strategy is to make each branch self-contained or start from a shared
checkpoint. More advanced strategies, such as hidden preroll or terminal
snapshots, can wait until the basic graph model proves useful.

The initial value is likely in authoring and validation rather than a broad
runtime:

- show a graph-aware dry run
- validate segment contracts before recording
- verify that branch targets are reachable from declared state
- keep narration and audio metadata attached to segments
- let the web player pause at explicit junctions

The older OmegaFlow prototype can remain as reference material for future graph
runtime ideas, especially node transitions, decision nodes, runtime/session
state, and subgraph composition. OmegaFlow should only absorb those ideas when they
serve the media workflow directly.
