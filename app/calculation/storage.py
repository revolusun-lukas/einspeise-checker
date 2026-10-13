"""Fiktiver Speicher: optimale Fahrweise bei bekannten Preisen (lineares Programm, HiGHS über scipy).

Der Speicher sitzt zwischen PV-Überschuss und Netz. Er lädt nur aus dem Überschuss (nicht aus dem
Netz) und speist später ein, wenn die Vergütung höher ist. Je Viertelstunde q:

    Einspeisung(q) = E(q) − laden(q) − abregeln(q) + entladen(q)
    Füllstand(q)   = Füllstand(q−1) + η_laden · laden(q) − entladen(q) / η_entladen,  Start leer
    0 ≤ Füllstand ≤ Kapazität,  laden ≤ min(E, Leistung/4),  entladen ≤ Leistung/4,  laden + abregeln ≤ E

Ziel: Σ Einspeisung(q) · Vergütung(q) maximieren. Die Lösung kennt alle Preise und den ganzen
PV-Verlauf im Voraus -- sie ist die Obergrenze dessen, was ein Speicher hätte bringen können.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

# Feste Annahmen (bewusst nicht einstellbar, siehe README):
C_RATE = 0.5  # Lade-/Entladeleistung in kW je kWh Kapazität (10 kWh -> 5 kW)
ROUND_TRIP_EFFICIENCY = 0.90
ETA_CHARGE = ETA_DISCHARGE = math.sqrt(ROUND_TRIP_EFFICIENCY)
SLOT_HOURS = 0.25

# Winzige Kosten je kWh Durchsatz bzw. Abregelung: Bei gleicher Vergütung (z.B. 0 ct) wäre die
# Fahrweise sonst beliebig -- so lädt der Speicher nur, wenn es sich lohnt, und regelt nur ab, wenn
# Einspeisen wirklich etwas kostet.
_TIE_BREAK_CT = 1e-4


class StorageError(Exception):
    pass


@dataclass
class StorageResult:
    feed_in_kwh: np.ndarray  # Netzeinspeisung mit Speicher
    charge_kwh: np.ndarray  # aus dem Überschuss eingespeichert
    discharge_kwh: np.ndarray  # aus dem Speicher eingespeist
    curtail_kwh: np.ndarray  # abgeregelt
    soc_kwh: np.ndarray  # Füllstand am Ende der Viertelstunde


def optimize_storage(
    surplus_kwh: np.ndarray,
    rate_ct_kwh: np.ndarray,
    capacity_kwh: float,
    allow_curtail: bool,
) -> StorageResult:
    """surplus_kwh: Überschuss je Viertelstunde (>= 0). rate_ct_kwh: Vergütung je kWh, NaN = kein
    Preis (dort wird weder geladen noch entladen). allow_curtail: Einspeisung darf abgeregelt werden."""
    n = len(surplus_kwh)
    surplus = np.clip(np.nan_to_num(surplus_kwh, nan=0.0), 0.0, None)
    priced = ~np.isnan(rate_ct_kwh)
    rate = np.where(priced, rate_ct_kwh, 0.0)
    step_kwh = C_RATE * capacity_kwh * SLOT_HOURS

    # Variablen: [laden (n) | entladen (n) | abregeln (n) | Füllstand (n)]
    i_c, i_d, i_x, i_s = 0, n, 2 * n, 3 * n
    # Minimieren: −Σ Vergütung · (−laden − abregeln + entladen) + Tie-Break
    cost = np.concatenate([
        rate + _TIE_BREAK_CT,
        -rate + _TIE_BREAK_CT,
        rate + _TIE_BREAK_CT,
        np.zeros(n),
    ])
    bounds = np.empty((4 * n, 2))
    bounds[i_c:i_d] = np.column_stack([np.zeros(n), np.where(priced, np.minimum(surplus, step_kwh), 0.0)])
    # Entladen nur bei positiver Vergütung: Bei Vergütung ≤ 0 bringt Einspeisen nichts, und ohne
    # Abregeln würde das LP sonst gleichzeitig laden und entladen, um Energie über die
    # Wirkungsgradverluste zu "vernichten" -- das kann ein echter Speicher nicht.
    bounds[i_d:i_x] = np.column_stack([np.zeros(n), np.where(priced & (rate > 0), step_kwh, 0.0)])
    bounds[i_x:i_s] = np.column_stack([np.zeros(n), surplus if allow_curtail else np.zeros(n)])
    bounds[i_s:] = (0.0, capacity_kwh)

    # Füllstand(q) − Füllstand(q−1) − η_c · laden(q) + entladen(q) / η_d = 0
    eye = sparse.identity(n, format="csr")
    shift = sparse.eye(n, k=-1, format="csr")
    a_eq = sparse.hstack([-ETA_CHARGE * eye, eye / ETA_DISCHARGE, sparse.csr_matrix((n, n)), eye - shift], format="csr")
    b_eq = np.zeros(n)

    # laden + abregeln ≤ Überschuss (nur nötig, wenn abgeregelt werden darf)
    a_ub = b_ub = None
    if allow_curtail:
        a_ub = sparse.hstack([eye, sparse.csr_matrix((n, n)), eye, sparse.csr_matrix((n, n))], format="csr")
        b_ub = surplus

    res = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.status != 0:
        raise StorageError(f"Speicher-Optimierung fehlgeschlagen: {res.message}")

    x = np.clip(res.x, 0.0, None)
    charge, discharge, curtail, soc = x[i_c:i_d], x[i_d:i_x], x[i_x:i_s], x[i_s:]
    feed_in = np.clip(surplus - charge - curtail + discharge, 0.0, None)
    return StorageResult(feed_in_kwh=feed_in, charge_kwh=charge, discharge_kwh=discharge, curtail_kwh=curtail, soc_kwh=soc)
