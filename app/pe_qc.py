"""QC + missing-data checks — design-agnostic.

Each check runs on the unified dataset and returns :class:`Finding` objects the UI renders.
Duplicate detection is **value-based**, never timestamp-based: two rows are a true duplicate
only if their measured values match; two scans of the same pot on the same day with different
values are a *repeat* (a repeatability pair), reported as such, not an error. Nothing is
altered or imputed.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_data  # noqa: E402  (for carrier_rescans; no circular import — pe_data imports only the reader)

SEV_ORDER = {"blocker": 0, "flag": 1, "warn": 2, "info": 3, "ok": 4}

_META = {"unit", "timestamp", "Week", "sensor", "genotype", "g_alias", "treatment",
         "block", "position", "plant", "date", "is_blank", "source_instance", "scan_key"}


@dataclass
class Finding:
    check: str
    severity: str          # blocker | flag | warn | info | ok
    message: str
    detail: pd.DataFrame | None = None
    explain: str = ""       # optional What / Why / What-to-do, shown in the expander body
    advanced: bool = False  # True = show under the Quality tab's "Advanced" section


def run_all(df: pd.DataFrame, gt_join: pd.DataFrame | None = None,
            factors: list[str] | None = None,
            measures: list[str] | None = None,
            candidates: pd.DataFrame | None = None) -> list[Finding]:
    findings: list[Finding] = []
    # core checks (prominent)
    findings += check_intervals(df)
    findings += check_repeat_scans(df)
    # carrier rescans are best seen on the pre-dedup candidate rows, if supplied
    findings += check_carrier_rescans(candidates if candidates is not None else df)
    findings += check_blanks_with_data(df)
    # advanced checks (rewritten, tucked away)
    findings += check_time_of_day(df)
    findings += check_aliasing(df, factors)
    findings += check_missing_measures(df, measures if measures is not None else _trait_cols(df))
    if gt_join is not None:
        findings += check_label_mismatch(gt_join)
    findings.sort(key=lambda f: SEV_ORDER.get(f.severity, 9))
    return findings


def _trait_cols(df):
    return [c for c in df.columns if c not in _META and pd.api.types.is_numeric_dtype(df[c])]


def _dates(df):
    return sorted(pd.to_datetime(df["date"].dropna().unique()))


# --------------------------------------------------------------------------- timing

def check_intervals(df) -> list[Finding]:
    d = _dates(df)
    if len(d) < 3:
        return [Finding("scan intervals", "info", f"{len(d)} scan date(s) — too few to judge spacing.")]
    gaps = np.diff([x.value for x in d]) / 86_400e9
    med = float(np.median(gaps))
    tab = pd.DataFrame({
        "from": [x.strftime("%Y-%m-%d") for x in d[:-1]],
        "to": [x.strftime("%Y-%m-%d") for x in d[1:]],
        "gap_days": gaps.round(1),
        "flag": np.where(gaps > 1.5 * med, ">1.5x median", ""),
    })
    if (tab["flag"] != "").any():
        return [Finding("scan intervals", "flag",
                        f"Uneven spacing (gaps {gaps.astype(int).tolist()} d, median {med:.0f}). "
                        f"Analyse on an elapsed-days axis, not a 'week' index.", tab)]
    return [Finding("scan intervals", "ok",
                    f"Spacing even (gaps {gaps.astype(int).tolist()} d).", tab)]


def check_time_of_day(df) -> list[Finding]:
    if "timestamp" not in df or df["timestamp"].isna().all():
        return []
    t = df.dropna(subset=["timestamp"]).copy()
    t["hour"] = t["timestamp"].dt.hour + t["timestamp"].dt.minute / 60
    t["elapsed"] = (t["date"] - t["date"].min()).dt.days
    out = []
    per_day = t.groupby("elapsed")["hour"].median()
    if len(per_day) >= 3:
        r = np.corrcoef(per_day.index, per_day.values)[0, 1]
        sev = "flag" if abs(r) > 0.6 else "ok"
        out.append(Finding("time-of-day drift", sev,
                           f"Median scan hour trends r={r:+.2f} with elapsed days.",
                           per_day.round(2).rename("median_hour").reset_index(),
                           explain=("**What:** the clock time of day you scan is sliding as the experiment "
                                    "goes on (e.g. afternoons early, mornings later).  \n"
                                    "**Why it matters:** leaves move, wilt and re-hydrate over a day, so a "
                                    "steady drift in scan time can masquerade as a real trend over time.  \n"
                                    "**What to do:** " + ("this series drifts — scan at a consistent time if you "
                                    "can, or keep hour-of-day as a covariate when modelling."
                                    if sev == "flag" else "no material drift here; nothing to do.")),
                           advanced=True))
    span = t.groupby("date")["hour"].agg(lambda h: h.max() - h.min())
    if (span > 6).any():
        out.append(Finding("long scan session", "warn",
                           f"{int((span > 6).sum())} date(s) span >6 h of scanning (up to {span.max():.0f} h).",
                           span[span > 6].round(1).rename("hour_span").reset_index(),
                           explain=("**What:** on some days scanning ran for many hours (sometimes into the "
                                    "night).  \n**Why it matters:** an unusually long or late session is often a "
                                    "setup/test run rather than a real timepoint, and early vs late plants on "
                                    "that day were imaged in different conditions.  \n**What to do:** check "
                                    "whether those dates are genuine timepoints; exclude setup runs in the "
                                    "Explore tab if not."),
                           advanced=True))
    return out


# --------------------------------------------------------------------------- duplicates

def check_repeat_scans(df) -> list[Finding]:
    """Value-based, not timestamp-based. Split (unit, date) groups of >1 row into identical
    duplicates (same values) vs repeats (values differ)."""
    sizes = df.groupby(["unit", "date"]).size()
    multi = sizes[sizes > 1].index
    if len(multi) == 0:
        return [Finding("repeat scans", "ok", "Every (unit, date) has a single record.")]
    tc = _trait_cols(df)
    idx = df.set_index(["unit", "date"]).sort_index()
    rows = []
    for key in multi:
        grp = idx.loc[[key]]
        vals = grp[tc].round(3) if tc else grp[[]]
        identical = bool(tc) and (vals.nunique(dropna=False) <= 1).all()
        rows.append({"unit": key[0], "date": pd.Timestamp(key[1]).strftime("%Y-%m-%d"),
                     "n_records": len(grp),
                     "kind": "identical duplicate" if identical else "repeat (values differ)"})
    detail = pd.DataFrame(rows)
    findings = []
    ndup = int((detail["kind"] == "identical duplicate").sum())
    nrep = int((detail["kind"] != "identical duplicate").sum())
    if ndup:
        findings.append(Finding("identical duplicates", "warn",
                                f"{ndup} (unit, date) had value-identical records in >1 row — a true "
                                f"duplicate (re-export or double write). One copy is kept.",
                                detail[detail["kind"] == "identical duplicate"]))
    if nrep:
        findings.append(Finding("repeat scans", "info",
                                f"{nrep} (unit, date) were scanned more than once with *different* values "
                                f"— a repeatability pair (a rescan), not an error. Kept; use to estimate "
                                f"measurement precision.",
                                detail[detail["kind"] != "identical duplicate"]))
    return findings


def check_carrier_rescans(df) -> list[Finding]:
    """A whole carrier (barcode) scanned two+ times on the same day — normally each carrier is
    scanned once a day, so this is worth a look. Value-based via distinct timestamps per (block,
    date)."""
    tab = pe_data.carrier_rescans(df)
    if len(tab):
        return [Finding("carrier scanned twice in a day", "warn",
                        f"{len(tab)} carrier-day(s): a whole barcode was scanned more than once on the same "
                        f"day — against the usual once-a-day pattern.",
                        tab,
                        explain=("**What:** the same carrier/barcode has two or more scan times on one "
                                 "date.  \n**Why it matters:** it usually means a rescan (jam, re-run, or a "
                                 "test pass) — you probably want only one of them in the analysis.  \n"
                                 "**What to do:** open the **Curate / resolve** tab to pick which scan to "
                                 "keep for that carrier and day."))]
    return [Finding("carrier scanned twice in a day", "ok",
                    "Each carrier was scanned at most once per day.")]


# --------------------------------------------------------------------------- integrity

def check_blanks_with_data(df) -> list[Finding]:
    if "is_blank" not in df:
        return []
    size_col = _size_col(df)
    if not size_col:
        return []
    bad = df[df["is_blank"] & (pd.to_numeric(df[size_col], errors="coerce") > 0)]
    if len(bad):
        return [Finding("blank slots with signal", "flag",
                        f"{len(bad)} scan(s) labelled blank carry {size_col} > 0 — a plant in a 'blank' "
                        f"slot, or a mislabelled carrier.",
                        bad[["unit", "date", size_col]].head(50))]
    return [Finding("blank slots with signal", "ok", "No blank slot carries a plant signal.")]


def check_aliasing(df, factors) -> list[Finding]:
    """Generic confound check: is any factor perfectly aliased with the scan date? (each of its
    levels only ever appears on one date). That makes its effect inseparable from time — often
    fine for a harvest cohort, but worth knowing before modelling."""
    if not factors:
        return []
    cats = [c for c in factors if c in df.columns and df[c].notna().any() and df[c].nunique() > 1]
    aliased = []
    for c in cats:
        sub = df[[c, "date"]].dropna()
        if sub.empty:
            continue
        per_level_dates = sub.groupby(c)["date"].nunique()
        if (per_level_dates <= 1).all():
            aliased.append(c)
    exp = ("**What:** a treatment factor's levels each appear on only one scan date.  \n"
           "**Why it matters:** you then cannot tell the factor's effect apart from whatever else changed "
           "on that date — they are confounded.  \n**What to do:** expected and fine for a staggered-harvest "
           "cohort (each group harvested on its own day); a problem only if the factor was meant to be "
           "followed over time.")
    if aliased:
        return [Finding("factor tied to one date", "warn",
                        f"Factor(s) {aliased} were each scanned on only one date.",
                        pd.DataFrame({"factor": aliased}), explain=exp, advanced=True)]
    return [Finding("factor tied to one date", "ok",
                    "No declared factor is confined to a single date.", explain=exp, advanced=True)]


def check_label_mismatch(gt_join) -> list[Finding]:
    if "label_mismatch" not in gt_join:
        return []
    exp = ("**What:** the treatment/genotype written in your manual-measurement file disagrees with the "
           "label on the scan at the same carrier:position.  \n**Why it matters:** if the labels don't "
           "match, the manual value may be joined to the wrong plant, corrupting any height/SPAD "
           "validation.  \n**What to do:** check those pots in the manual file and the design; fix the "
           "mislabelled side before trusting the ground-truth link.")
    bad = gt_join[gt_join["label_mismatch"] == True]  # noqa: E712
    if len(bad):
        return [Finding("manual vs scan labels", "flag",
                        f"{len(bad)} pot(s): manual-file labels disagree with the scan's labels.",
                        bad[["block", "position", "date"]].head(50), explain=exp, advanced=True)]
    return [Finding("manual vs scan labels", "ok",
                    "Manual-file labels agree with scan labels.", explain=exp, advanced=True)]


# --------------------------------------------------------------------------- missing data

def missingness_summary(df: pd.DataFrame, measures: list[str]) -> pd.DataFrame:
    """% missing per measurement column, with a status. 'EMPTY' = produced no values at all
    (e.g. a SPAD column that never recorded a number)."""
    n = len(df)
    rows = []
    for c in measures:
        s = df[c]
        miss = int(s.isna().sum())
        pct = round(100 * miss / n, 1) if n else 0.0
        status = ("EMPTY — produced no values" if miss == n
                  else "mostly missing" if pct > 50
                  else "some missing" if miss else "complete")
        rows.append({"measure": c, "present": n - miss, "missing": miss,
                     "pct_missing": pct, "status": status})
    return pd.DataFrame(rows).sort_values("pct_missing", ascending=False).reset_index(drop=True)


def missingness_by_group(df: pd.DataFrame, measure: str, group: str) -> pd.DataFrame:
    """% missing of one measure across the levels of a grouping column (date, treatment, …)."""
    g = df.groupby(group)[measure]
    out = pd.DataFrame({
        "n": g.size(),
        "present": g.apply(lambda s: int(s.notna().sum())),
        "pct_missing": g.apply(lambda s: round(100 * s.isna().mean(), 1)),
    }).reset_index()
    return out


def check_missing_measures(df: pd.DataFrame, measures: list[str]) -> list[Finding]:
    """Flag columns that produced no values, and note the MNAR stance (no imputation)."""
    n = len(df)
    empty = [c for c in measures if df[c].isna().all()]
    partial = [c for c in measures if 0 < df[c].isna().sum() < n]
    exp = ("**What:** some measurement columns are missing values (or produced none at all).  \n"
           "**Why it matters:** a measure that never recorded anything can't be analysed, and a group or "
           "occasion that is entirely absent is 'missing not at random' — imputing it would invent data.  \n"
           "**What to do:** see the Missing-data tab for the exact pattern; the app never fills these in.")
    out = []
    if empty:
        out.append(Finding("empty measurements", "flag",
                           f"{len(empty)} measure(s) produced no values at all: "
                           f"{', '.join(empty[:8])}{'…' if len(empty) > 8 else ''}.",
                           pd.DataFrame({"empty_measure": empty}), explain=exp, advanced=True))
    if partial:
        out.append(Finding("partially missing measurements", "info",
                           f"{len(partial)} measure(s) are partly missing.",
                           None, explain=exp, advanced=True))
    if not out:
        out.append(Finding("measurement completeness", "ok",
                           "No measurement column is missing values.", explain=exp, advanced=True))
    return out


# ----------------------------------------------------------------- row-level anomalies

def anomalous_scans(df: pd.DataFrame, measure: str, include_zero: bool = True) -> pd.DataFrame:
    """The *specific* scans where ``measure`` is missing (NaN) or zero, with full identity so you
    can go back to the raw data or curate them. Answers 'day 30 shows 0 for many plants — which?'"""
    if measure not in df.columns:
        return pd.DataFrame()
    s = pd.to_numeric(df[measure], errors="coerce")
    mask = s.isna() | (include_zero & (s == 0))
    bad = df[mask].copy()
    if bad.empty:
        return pd.DataFrame()
    bad["value"] = s[mask]
    bad["status"] = np.where(bad["value"].isna(), "missing", "zero")
    cols = ["block", "position", "unit", "date", "timestamp", "status", "value", "source_instance"]
    out = bad[[c for c in cols if c in bad.columns]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    if "timestamp" in out:
        out["timestamp"] = pd.to_datetime(out["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    return out.sort_values(["date", "block", "position"]).reset_index(drop=True)


def timeseries_outliers(df: pd.DataFrame, trait: str, k: float = 3.5,
                        min_points: int = 4) -> pd.DataFrame:
    """Per plant (``unit``), flag a timepoint whose value sits far from its own temporal
    neighbours — e.g. a spike or a drop to near-zero that recovers later. Robust: residual of
    each interior point vs the straight line through its two neighbours, thresholded at
    ``k``·(normal-consistent MAD) of that plant's residuals. Returns each outlier's identity."""
    if trait not in df.columns:
        return pd.DataFrame()
    d = df.dropna(subset=[trait]).copy()
    if d.empty:
        return pd.DataFrame()
    d["elapsed"] = (d["date"] - d["date"].min()).dt.days
    rows = []
    for unit, g in d.groupby("unit"):
        g = g.sort_values("elapsed")
        if len(g) < min_points:
            continue
        x = g["elapsed"].to_numpy(float)
        y = g[trait].to_numpy(float)
        resid = np.zeros(len(g))
        for i in range(1, len(g) - 1):
            x0, x1, x2 = x[i - 1], x[i], x[i + 1]
            y0, y2 = y[i - 1], y[i + 1]
            pred = y0 + (y2 - y0) * ((x1 - x0) / (x2 - x0)) if x2 != x0 else (y0 + y2) / 2
            resid[i] = y[i] - pred
        mad = np.median(np.abs(resid - np.median(resid))) or 1e-9
        thr = k * 1.4826 * mad
        for i in range(1, len(g) - 1):
            if abs(resid[i]) > thr:
                r = g.iloc[i]
                rows.append({
                    "unit": unit, "block": r["block"], "position": r["position"],
                    "date": pd.Timestamp(r["date"]).strftime("%Y-%m-%d"),
                    "timestamp": pd.Timestamp(r["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                    "value": round(float(y[i]), 2),
                    "expected_from_neighbours": round(float(y[i] - resid[i]), 2),
                    "source_instance": r.get("source_instance", ""),
                })
    return (pd.DataFrame(rows).sort_values(["date", "unit"]).reset_index(drop=True)
            if rows else pd.DataFrame())


# --------------------------------------------------------------------------- helpers

def _size_col(df):
    for c in df.columns:
        cl = c.lower()
        if ("digital biomass" in cl or "leaf area" in cl) and pd.api.types.is_numeric_dtype(df[c]):
            return c
    return None
