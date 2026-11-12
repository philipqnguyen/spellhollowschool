"""Skin weight helpers: chain blends, normalisation and smoothing."""

from __future__ import annotations

import numpy as np

from . import sdf as S

MAX_INFLUENCES = 4
MIN_WEIGHT = 0.01

Weights = dict[str, float]


def chain(s: float, bones: list[str], bounds: list[float], blends: list[float]) -> Weights:
    """Weights along a bone chain from a scalar position s.

    `bounds[i]` is where bones[i] hands over to bones[i + 1], blended over
    +-blends[i]. The weights telescope, so they always sum to 1.
    """
    out: Weights = {}
    prev = 1.0  # weight that has not been handed to later bones yet
    for i, name in enumerate(bones):
        if i < len(bounds):
            handed = float(S.smoothstep(bounds[i] - blends[i], bounds[i] + blends[i], s))
            w = prev * (1.0 - handed)
            prev = prev * handed
        else:
            w = prev
        if w > 1e-6:
            out[name] = out.get(name, 0.0) + w
    return out


def mix(*parts: tuple[float, Weights]) -> Weights:
    out: Weights = {}
    for k, w in parts:
        if k <= 0:
            continue
        for name, v in w.items():
            out[name] = out.get(name, 0.0) + k * v
    return out


def finalise(w: Weights) -> Weights:
    """Normalise, keep the strongest four influences, drop tiny ones."""
    items = sorted(((v, k) for k, v in w.items() if v > 0), reverse=True)[:MAX_INFLUENCES]
    total = sum(v for v, _ in items)
    if total <= 0:
        return {}
    items = [(v / total, k) for v, k in items]
    items = [(v, k) for v, k in items if v >= MIN_WEIGHT]
    total = sum(v for v, _ in items)
    return {k: v / total for v, k in items}


def smooth(weights: list[Weights], neighbours: list[np.ndarray], ids, iters: int = 3, keep: float = 0.5) -> None:
    """Laplacian smoothing of weight dicts over the given vertex ids (in place)."""
    ids = list(ids)
    for _ in range(iters):
        new = {}
        for i in ids:
            nb = neighbours[i]
            if len(nb) == 0:
                continue
            acc: Weights = {k: v * keep for k, v in weights[i].items()}
            share = (1.0 - keep) / len(nb)
            for j in nb:
                for k, v in weights[j].items():
                    acc[k] = acc.get(k, 0.0) + v * share
            new[i] = acc
        for i, w in new.items():
            weights[i] = w


def side_name(bone: str, side: int) -> str:
    return f"{bone}.{'L' if side > 0 else 'R'}"
