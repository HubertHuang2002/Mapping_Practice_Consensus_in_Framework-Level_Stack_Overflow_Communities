"""Circle-pack geometry for the breakdown: non-overlapping circles whose AREA encodes authority
at both levels — outer (cluster) = authority mass, inner (answer) = each point's authority. Pure
geometry, no domain logic, no I/O. Cluster order is decided by the caller and preserved here.
"""
from __future__ import annotations

import circlify

from contract.types import Breakdown, PracticePoint


def pack(breakdown: Breakdown) -> Breakdown:
    """Fill x/y/r on every point and cluster shell via a 2-level circle pack (cluster → its
    points), sized by authority. Mutates and returns the breakdown."""
    pts_by_cluster: dict[str, list[PracticePoint]] = {}
    for p in breakdown.points:
        pts_by_cluster.setdefault(p.cluster, []).append(p)

    data = []
    for shell in breakdown.clusters:
        # size = the fused weight W (always present: A-absent points degrade to Q, so they're sized by
        # their answer-level vote, not zeroed). floor at 1e-6 so a W=0 point still has a positive area.
        kids = [{"id": id(p), "datum": max(p.weight, 1e-6)} for p in pts_by_cluster[shell.id]]
        data.append({"id": shell.id, "datum": sum(k["datum"] for k in kids), "children": kids})

    circles = circlify.circlify(
        data, show_enclosure=False, target_enclosure=circlify.Circle(x=0, y=0, r=1)
    )

    point_by_key = {id(p): p for ps in pts_by_cluster.values() for p in ps}
    shell_by_id = {s.id: s for s in breakdown.clusters}
    for c in circles:
        if c.level == 1:
            s = shell_by_id[c.ex["id"]]
            s.x, s.y, s.r = c.x, c.y, c.r
        else:
            p = point_by_key[c.ex["id"]]
            p.x, p.y, p.r = c.x, c.y, c.r
    return breakdown
