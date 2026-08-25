"""Design-workbook browser — design-agnostic and user-driven.

An optional convenience. If the user supplies a design/layout spreadsheet, this loads every
sheet and *suggests* which columns are treatment factors, but the user confirms — the app
never asserts a structure it guessed. It then reports levels, factorial cells, and replication,
and (optionally) a split-plot hierarchy from nesting columns the user picks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Longer, unambiguous fragments matched anywhere in a column name.
_SUB_EXCLUDE = ("scan", "barcode", "carrier", "mainunit", "subunit", "meta", "index",
                "coord", "slot", "table", "bench", "block", "plot", "pair", "column",
                "replicate", "replication")
# Short/ambiguous tokens matched only as whole words (so "row" doesn't hit "g-row-thstage").
_EXACT_EXCLUDE = {"unit", "main", "sub", "row", "col", "pos", "id", "rep", "pot", "x", "y",
                  "no", "idx", "position"}
_BLANKS = {"blank", "empty", "", "nan", "none", "-", "na", "n/a"}


def _name_tokens(name: str) -> list[str]:
    """Split a column name on delimiters and camelCase, lowercased."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name))
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


def _is_coord(name: str) -> bool:
    cl = str(name).lower()
    if any(tok in cl for tok in _SUB_EXCLUDE):
        return True
    return any(t in _EXACT_EXCLUDE for t in _name_tokens(name))


@dataclass
class Workbook:
    path: Path
    sheets: list
    frames: dict  # sheet name -> DataFrame


def load_workbook(path: str | Path) -> Workbook:
    path = Path(path)
    xl = pd.ExcelFile(path)
    frames = {}
    for s in xl.sheet_names:
        df = pd.read_excel(xl, s)
        df.columns = [str(c).strip() for c in df.columns]
        frames[s] = df
    return Workbook(path=path, sheets=list(xl.sheet_names), frames=frames)


def suggest_layout_sheet(wb: Workbook) -> str | None:
    """The tallest sheet that has at least a couple of factor-like columns."""
    best, best_score = None, -1
    for s, df in wb.frames.items():
        if df.empty:
            continue
        nf = len(suggest_factors(df))
        score = len(df) if nf >= 2 else (1 if nf >= 1 else 0)
        if score > best_score:
            best, best_score = s, score
    return best or (wb.sheets[0] if wb.sheets else None)


def suggest_factors(df: pd.DataFrame, max_levels: int = 20) -> list[str]:
    """Guess treatment-factor columns: low-cardinality, not coordinates/ids/structure."""
    out, n = [], len(df)
    for c in df.columns:
        if _is_coord(c):
            continue
        s = df[c]
        nun = s.nunique(dropna=True)
        if nun < 2 or nun > max_levels or nun == n:
            continue
        if pd.api.types.is_numeric_dtype(s) and not (s.dropna() % 1 == 0).all():
            continue
        out.append(c)
    return out


def factor_levels(df: pd.DataFrame, cols: list[str]) -> dict:
    out = {}
    for c in cols:
        lv = [str(v) for v in df[c].dropna().unique() if str(v).strip().lower() not in _BLANKS]
        out[c] = sorted(lv)
    return out


def summarize(df: pd.DataFrame, factor_cols: list[str]) -> dict:
    lv = factor_levels(df, factor_cols)
    cells = 1
    for v in lv.values():
        cells *= max(len(v), 1)
    n = len(df)
    return {"levels": lv, "cells": cells, "units": n,
            "reps": (n / cells if cells else None)}


def detect_hierarchy(df: pd.DataFrame, nesting_cols: list[str], factor_cols: list[str]) -> list:
    """Given nesting columns coarsest→finest, report what each level holds constant."""
    levels, cols, prev = [], [], set()
    for c in nesting_cols:
        cols.append(c)
        g = df.groupby(cols)
        const = [f for f in factor_cols if f in df.columns and g[f].nunique().max() == 1]
        levels.append({
            "level": c,
            "groups": g.ngroups,
            "units_per_group": sorted(set(g.size().tolist()))[:4],
            "constant": const,
            "introduces": [f for f in const if f not in prev],
        })
        prev = set(const)
    return levels
