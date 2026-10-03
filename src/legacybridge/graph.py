"""Dependency graph over modules, collapsed into migration units (strongly connected components).

Edges point from a dependent to its dependency (CALLER -> CALLEE, PROGRAM -> COPYBOOK).
A cycle of CALLs becomes one migration unit, translated and verified together.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx


@dataclass
class MigrationPlan:
    graph: nx.DiGraph                                # module-level graph
    units: dict[str, list[str]]                      # unit name -> member modules
    unit_of: dict[str, str]                          # module -> unit
    unit_graph: nx.DiGraph                           # unit-level DAG (dependent -> dependency)
    order: list[list[str]] = field(default_factory=list)  # waves of units, dependencies first
    cycles: list[list[str]] = field(default_factory=list)
    external: list[str] = field(default_factory=list)     # called but not found in the project

    def deps_of(self, unit: str) -> list[str]:
        return sorted(self.unit_graph.successors(unit))

    def dependents_of(self, unit: str) -> list[str]:
        return sorted(self.unit_graph.predecessors(unit))

    def transitive_dependents(self, unit: str) -> set[str]:
        return set(nx.ancestors(self.unit_graph, unit))


def unit_name(members: list[str]) -> str:
    return "+".join(sorted(members))


def build_plan(modules: dict[str, dict], edges: list[tuple[str, str, str]]) -> MigrationPlan:
    """modules: name -> {'kind': ...}; edges: (src, dst, kind) with src depending on dst."""
    g = nx.DiGraph()
    for name, attrs in modules.items():
        g.add_node(name, **attrs)
    external = sorted({dst for _, dst, _ in edges if dst not in modules})
    for name in external:
        g.add_node(name, kind="external")
    for src, dst, kind in edges:
        g.add_edge(src, dst, kind=kind)

    sccs = [sorted(c) for c in nx.strongly_connected_components(g)]
    units = {unit_name(c): c for c in sccs}
    unit_of = {m: u for u, ms in units.items() for m in ms}
    cycles = [c for c in sccs if len(c) > 1]

    ug = nx.DiGraph()
    ug.add_nodes_from(units)
    for src, dst in g.edges:
        us, ud = unit_of[src], unit_of[dst]
        if us != ud:
            ug.add_edge(us, ud)
    # waves: reverse topological generations (dependencies first)
    order = [sorted(gen) for gen in nx.topological_generations(ug.reverse(copy=True))]
    return MigrationPlan(graph=g, units=units, unit_of=unit_of, unit_graph=ug, order=order, cycles=cycles,
                         external=external)
