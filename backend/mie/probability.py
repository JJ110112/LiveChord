"""Step 4 of the pipeline: dynamic restraint and the probability gate (plan §3, §6, §4-10)."""

from __future__ import annotations

from random import Random
from typing import Iterable

from .graph import Edge


def restraint(human_energy: float, curve: float = 1.0) -> float:
    """1.0 when the human is silent, ->0 when they are playing hard.
    curve 1 = linear, 2 = more conservative, 0.5 = more eager."""
    e = max(0.0, min(1.0, human_energy))
    return (1.0 - e) ** max(0.05, curve)


def p_eff(edge: Edge, globals_: dict, human_energy: float, mode_caps: dict | None = None) -> float:
    scale = float(globals_.get("prob_scale", 1.0))
    if mode_caps and "prob_scale_max" in mode_caps:
        scale = min(scale, float(mode_caps["prob_scale_max"]))
    r = restraint(human_energy, float(globals_.get("restraint_curve", 1.0))) * float(globals_.get("restraint", 1.0)) \
        + (1.0 - float(globals_.get("restraint", 1.0)))
    return max(0.0, min(1.0, edge.prob * scale * r))


def roll(edges: Iterable[Edge], globals_: dict, human_energy: float, rng: Random,
         mode_caps: dict | None = None) -> list[tuple[Edge, float]]:
    """Return the edges that fire this time, with the probability they cleared.

    Edges sharing `group_id` form a weighted roulette: one roll picks a single
    edge by weight (prob), then that edge alone is tested against p_eff.
    """
    groups: dict[str, list[Edge]] = {}
    singles: list[Edge] = []
    for e in edges:
        gid = e.params.get("group_id")
        if gid:
            groups.setdefault(str(gid), []).append(e)
        else:
            singles.append(e)
    fired: list[tuple[Edge, float]] = []
    for e in singles:
        p = p_eff(e, globals_, human_energy, mode_caps)
        if p > 0 and rng.random() < p:
            fired.append((e, p))
    for members in groups.values():
        weights = [max(0.0, m.prob) for m in members]
        tot = sum(weights)
        if tot <= 0:
            continue
        x = rng.random() * tot
        chosen = members[-1]
        for m, w in zip(members, weights):
            if x < w:
                chosen = m
                break
            x -= w
        p = p_eff(chosen, globals_, human_energy, mode_caps)
        if p > 0 and rng.random() < p:
            fired.append((chosen, p))
    return fired
