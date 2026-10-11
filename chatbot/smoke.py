"""Offline smoke test: run the whole pipeline on synthetic layers (no network).

    python -m chatbot.smoke

Used by the GitHub Actions workflow; also handy to check an installation.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from .csdi import LayerData
from .grid import assign_codes, encode_vectorised
from .llm import RuleBasedAgent
from .tools import LoadedLayer, Session


def synthetic_layer(name: str, n: int, seed: int, cluster_centres: np.ndarray) -> LayerData:
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(cluster_centres), n)
    xy = cluster_centres[pick] + rng.normal(0, 400, (n, 2))
    districts = np.array(["KOWLOON-YAU TSIM MONG", "HONG KONG ISLAND-CENTRAL AND WESTERN", "NEW TERRITORIES-SHA TIN"])
    df = pd.DataFrame(
        {
            "OBJECTID": np.arange(1, n + 1),
            "NAME_EN": [f"{name} {i}" for i in range(n)],
            "SEARCH02_EN": districts[pick % len(districts)],
            "NSEARCH06_EN": rng.integers(1, 20, n),
            "easting": xy[:, 0],
            "northing": xy[:, 1],
        }
    )
    fields = [{"name": c, "type": "esriFieldTypeString", "alias": c} for c in df.columns if c not in ("easting", "northing")]
    return LayerData(name=name, url=f"synthetic://{name}", df=df, fields=fields, reported_count=n)


def main() -> int:
    centres = np.array([[835_500.0, 817_500.0], [836_500.0, 820_500.0], [838_000.0, 826_000.0], [820_000.0, 832_000.0]])
    a = synthetic_layer("Layer A", 1500, 1, centres)
    b = synthetic_layer("Layer B", 1200, 2, centres)

    assert encode_vectorised(np.array([856793.009]), np.array([832366.405]), 6)[0] == "WG73JD"

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(output_dir=Path(tmp))
        session.permutations = 49
        for key, raw in (("A", a), ("B", b)):
            df, dropped = assign_codes(raw.df)
            session.layers[key] = LoadedLayer(key, raw.name, raw.url, raw, df, dropped)

        agent = RuleBasedAgent(session)
        checks = {
            "summary": "binned to",
            "correlation": "Moran",
            "correlation at 2 km": "2 km district cells",
            "bar chart of layer b by district": "SEARCH02_EN",
            "histogram of hotspots for layer b": "NSEARCH06_EN",
            "which cells have layer a but no layer b": "contain Layer A but no Layer B",
            "top 3 cells for layer a": "top 3",
            "what is in cell H8": "HKGeoCode H8",
            "show the map": "Map of Layer A",
            "what should we do next": "Suggested actions",
        }
        failures = 0
        for question, expected in checks.items():
            reply = agent.respond(question)
            ok = expected in reply.text
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {question!r} -> {reply.tool_calls}")
            if not ok:
                print(reply.text[:500])
        assert (Path(tmp) / "chatbot_map_4.html").exists(), "map was not written"
        r = session.correlation(4)
        assert -1 <= r.pearson_r <= 1 and 0 < r.moran["p_value"] <= 1
        print(f"Moran's I = {r.moran['I']:.3f}, Jaccard = {r.jaccard:.3f}, cells = {len(r.cells):,}")
    print("smoke test", "FAILED" if failures else "passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
