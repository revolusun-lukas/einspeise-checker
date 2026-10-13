"""CSV-Einlesen sowie Spalten-/Werte-Typ-Erkennung für unterschiedliche Shelly-/Smartmeter-Exporte.

Übernommen aus dem Dynamischen Tarif Check, ergänzt um die Erkennung der Einspeise-Spalte
(z.B. Shelly "Returned energy"), der Werte-Richtung (reine Einspeisung vs. Netto) und einer
optionalen Bezugsspalte."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

import pandas as pd

ValueType = Literal["power_w", "power_kw", "energy_wh", "energy_kwh", "counter_kwh"]
# feed_in: Spalte enthält nur die Einspeisung (positiv). net: Netto-Werte, positiv = Bezug,
# negativ = Einspeisung (z.B. Momentanleistung eines Zweirichtungszählers).
Direction = Literal["feed_in", "net"]

TIMESTAMP_NAME_HINTS = ["zeit", "datum", "time", "date", "timestamp", "ts"]

VALUE_NAME_HINTS = {
    "power_w": ["power", "watt", "leistung"],
    "power_kw": ["kw", "kilowatt"],
    "counter_kwh": ["zaehlerstand", "zählerstand", "total", "counter", "meter", "stand", "zaehler", "zähler"],
    "energy_wh": ["wh"],
    "energy_kwh": ["kwh"],
    "generic": ["energy", "energie", "verbrauch", "consumption", "value", "wert"],
}

# Extra-Gewicht fuer Spaltennamen, die auf einen Gesamt-/Summenwert hindeuten (z.B. "Summe Verbrauch"
# vs. "Direktverbrauch"/"Batterie Entladen"/"Netzbezug"): ohne diesen Bonus binden sich Teil- und
# Gesamtspalten bei der Namens-Bewertung (beide treffen z.B. den generischen "verbrauch"-Hint), und die
# zufaellige Spaltenreihenfolge in der CSV entscheidet -- das fuehrt dazu, dass eine Teilmenge des
# Verbrauchs statt des tatsaechlichen Gesamtverbrauchs vorgeschlagen wird.
TOTAL_NAME_HINTS = ["summe", "gesamt", "total", "sum"]

_UNIX_S_MIN, _UNIX_S_MAX = 1_000_000_000, 4_000_000_000
_UNIX_MS_MIN, _UNIX_MS_MAX = 1_000_000_000_000, 4_000_000_000_000

_GERMAN_DT_PATTERNS = [
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
]

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DECIMAL_COMMA_RE = re.compile(r"^-?\d+,\d+$")
_DECIMAL_DOT_RE = re.compile(r"^-?\d+\.\d+$")


class ParsingError(Exception):
    pass


# Spaltennamen und Zellwerte werden im Frontend als Vorschau angezeigt -- erkennt
# HTML-aehnliche Fragmente (z.B. "<img ...>", "<script>", "</a>"), die dort andernfalls
# als Markup statt als Text gerendert werden koennten. Kein Ersatz fuer sauberes
# Escaping im Frontend (Defense in Depth), sondern ein zusaetzlicher, sichtbarer Warnhinweis.
_HTML_LIKE_RE = re.compile(r"<\s*[a-zA-Z!/]")


def find_html_like_values(values: list[str]) -> list[str]:
    """Liefert die (bis zu 3) ersten Werte, die HTML-aehnliche Fragmente enthalten."""
    hits = [v for v in values if v and _HTML_LIKE_RE.search(v)]
    return hits[:3]


def sniff_delimiter(text_sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(text_sample, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except csv.Error:
        first_line = text_sample.splitlines()[0] if text_sample.splitlines() else ""
        counts = {d: first_line.count(d) for d in [",", ";", "\t", "|"]}
        if any(counts.values()):
            return max(counts, key=counts.get)
        return ","


def _looks_like_decimal_comma(values: list[str]) -> bool:
    comma_hits = sum(1 for v in values if _DECIMAL_COMMA_RE.match(v.strip()))
    dot_hits = sum(1 for v in values if _DECIMAL_DOT_RE.match(v.strip()))
    return comma_hits > 0 and comma_hits >= dot_hits


def read_csv_raw(content: bytes) -> tuple[pd.DataFrame, dict]:
    """Liest die Roh-CSV als String-DataFrame ein und schätzt Trennzeichen/Dezimaltrennzeichen."""
    try:
        text = content.decode("utf-8-sig", errors="replace")
    except Exception as exc:  # pragma: no cover - decode with errors="replace" practically never raises
        raise ParsingError(f"Datei konnte nicht als Text gelesen werden: {exc}") from exc

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ParsingError("Die Datei enthält keine verwertbaren Zeilen (Header + mindestens eine Datenzeile nötig).")

    sample = "\n".join(lines[:20])
    delimiter = sniff_delimiter(sample)

    decimal = "."
    header = lines[0].split(delimiter)
    data_lines = [l.split(delimiter) for l in lines[1:21]]
    for col_idx in range(len(header)):
        col_values = [row[col_idx] for row in data_lines if col_idx < len(row)]
        if _looks_like_decimal_comma(col_values):
            decimal = ","
            break

    try:
        df = pd.read_csv(io.StringIO(text), sep=delimiter, engine="python", dtype=str)
    except Exception as exc:
        raise ParsingError(f"CSV konnte nicht geparst werden ({exc}). Bitte Format prüfen.") from exc

    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    return df, {"delimiter": delimiter, "decimal": decimal}


def parse_numeric(raw: Optional[str], decimal: str) -> Optional[float]:
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    if decimal == ",":
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_timestamp_value(raw: Optional[str], tz: ZoneInfo) -> Optional[datetime]:
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None

    if _NUMERIC_RE.match(raw):
        num = float(raw)
        if _UNIX_MS_MIN <= abs(num) <= _UNIX_MS_MAX:
            return datetime.fromtimestamp(num / 1000, tz=timezone.utc)
        if _UNIX_S_MIN <= abs(num) <= _UNIX_S_MAX:
            return datetime.fromtimestamp(num, tz=timezone.utc)
        return None

    for fmt in _GERMAN_DT_PATTERNS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue

    try:
        ts = pd.Timestamp(raw)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    dt = ts.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _sample_values(series: pd.Series, n: int = 30) -> list[str]:
    non_null = series.dropna()
    return [str(v) for v in non_null.head(n).tolist()]


def suggest_timestamp_column(df: pd.DataFrame) -> tuple[Optional[str], list[str]]:
    """Wählt die Spalte mit der besten Erfolgsquote beim Zeitstempel-Parsen (+ Namens-Bonus)."""
    tz = ZoneInfo("Europe/Berlin")  # nur für den Erkennungs-Testparse; die tatsächliche Zeitzone wählt der Nutzer später
    warnings: list[str] = []
    best_col: Optional[str] = None
    best_score = -1.0

    for col in df.columns:
        sample = _sample_values(df[col])
        if not sample:
            continue
        successes = sum(1 for v in sample if parse_timestamp_value(v, tz) is not None)
        ratio = successes / len(sample)
        name_bonus = 0.15 if any(h in col.lower() for h in TIMESTAMP_NAME_HINTS) else 0.0
        score = ratio + name_bonus
        if ratio >= 0.7 and score > best_score:
            best_score = score
            best_col = col

    if best_col is None:
        warnings.append("Es konnte keine Zeitstempel-Spalte automatisch erkannt werden. Bitte manuell auswählen.")
    return best_col, warnings


def _name_score(col_name: str) -> float:
    name = col_name.lower()
    score = 0.0
    for hints in VALUE_NAME_HINTS.values():
        if any(h in name for h in hints):
            score += 1.0
    if any(h in name for h in TOTAL_NAME_HINTS):
        score += 2.0
    return score


def _numeric_candidates(df: pd.DataFrame, exclude: list[str]) -> list[str]:
    candidates = []
    for col in df.columns:
        if col in exclude:
            continue
        sample = _sample_values(df[col])
        if not sample:
            continue
        numeric_hits = sum(
            1 for v in sample if parse_numeric(v, ",") is not None or parse_numeric(v, ".") is not None
        )
        ratio = numeric_hits / len(sample)
        if ratio >= 0.8:
            candidates.append(col)
    return candidates


def suggest_value_column(df: pd.DataFrame, exclude: Optional[str]) -> Optional[str]:
    """Wählt unter den (mutmaßlich) numerischen Spalten diejenige mit dem höchsten Namens-Score."""
    candidates = _numeric_candidates(df, exclude=[exclude] if exclude else [])
    if not candidates:
        return None
    candidates.sort(key=_name_score, reverse=True)
    return candidates[0]


def suggest_value_type(col_name: str, series: pd.Series, decimal: str) -> ValueType:
    """Schätzt anhand von Spaltenname + Monotonie, welche der fünf Werte-Bedeutungen vorliegt."""
    name = col_name.lower()

    if any(h in name for h in VALUE_NAME_HINTS["power_kw"]) and "kwh" not in name:
        return "power_kw"
    if any(h in name for h in VALUE_NAME_HINTS["power_w"]) and "kwh" not in name:
        return "power_w"
    values = [parse_numeric(v, decimal) for v in series.tolist()]
    values = [v for v in values if v is not None]

    # "total" steckt auch in Intervall-Spalten wie Shellys "total_act_ret_energy" (Wh je Minute) --
    # als Zählerstand daher nur, wenn die Werte tatsächlich (fast) monoton steigen.
    if any(h in name for h in VALUE_NAME_HINTS["counter_kwh"]) and _is_mostly_monotonic(values):
        return "counter_kwh"
    if any(h in name for h in VALUE_NAME_HINTS["energy_wh"]) and "kwh" not in name:
        return "energy_wh"

    if any(h in name for h in VALUE_NAME_HINTS["energy_kwh"]):
        if _is_mostly_monotonic(values):
            return "counter_kwh"
        return "energy_kwh"

    if _is_mostly_monotonic(values) and values and (max(values) - min(values)) > 0:
        return "counter_kwh"

    if values:
        avg = sum(values) / len(values)
        if avg > 50:
            return "power_w"
        if avg > 5:
            return "energy_wh"
    return "energy_kwh"


def _is_mostly_monotonic(values: list[float], tolerance: float = 0.05) -> bool:
    if len(values) < 5:
        return False
    increases = 0
    total = 0
    for a, b in zip(values, values[1:]):
        total += 1
        if b >= a:
            increases += 1
    return total > 0 and (increases / total) >= (1 - tolerance)


# --- Einspeisung -------------------------------------------------------------------------

FEED_IN_NAME_HINTS = [
    "returned", "return", "_ret", "ret_", "einspeis", "export", "feed", "lieferung", "rückspeis",
    "rueckspeis", "erzeugung",
]
GRID_NAME_HINTS = ["active energy", "act_energy", "bezug", "import", "consumption", "verbrauch", "purchase"]


def _has_feed_in_hint(col_name: str) -> bool:
    name = col_name.lower()
    return any(h in name for h in FEED_IN_NAME_HINTS)


def _has_negative_values(series: pd.Series, decimal: str, sample_size: int = 5000) -> bool:
    # Gleichmäßig über die ganze Datei verteilt -- die ersten Zeilen sind oft Nacht ohne Einspeisung.
    non_null = series.dropna()
    step = max(1, len(non_null) // sample_size)
    values = [parse_numeric(v, decimal) for v in non_null.iloc[::step].tolist()]
    return any(v is not None and v < 0 for v in values)


def suggest_feed_in_column(df: pd.DataFrame, exclude: Optional[str], decimal: str) -> tuple[Optional[str], Direction]:
    """Schlägt die Einspeise-Spalte und ihre Richtung vor.

    Bevorzugt Spalten, deren Name auf Einspeisung hindeutet ("Returned energy", "Einspeisung",
    "total_act_ret_energy") -- die enthalten die Einspeisung bereits positiv. Sonst die beste
    Werte-Spalte; hat sie negative Werte, ist es vermutlich eine Netto-Spalte (negativ = Einspeisung).
    """
    candidates = _numeric_candidates(df, exclude=[exclude] if exclude else [])
    if not candidates:
        return None, "feed_in"
    feed_in = [c for c in candidates if _has_feed_in_hint(c)]
    if feed_in:
        feed_in.sort(key=_name_score, reverse=True)
        return feed_in[0], "feed_in"
    candidates.sort(key=_name_score, reverse=True)
    col = candidates[0]
    return col, "net" if _has_negative_values(df[col], decimal) else "feed_in"


def suggest_grid_column(df: pd.DataFrame, exclude: list[str]) -> Optional[str]:
    """Optionale Bezugsspalte (z.B. Shelly "Active energy"), nur bei eindeutigem Namen."""
    for col in _numeric_candidates(df, exclude=exclude):
        name = col.lower()
        if not _has_feed_in_hint(col) and any(h in name for h in GRID_NAME_HINTS):
            return col
    return None


def suggest_timezone(timestamp_col: Optional[str]) -> str:
    """Shelly EM exportiert z.B. "Date/time UTC" -- dann ist UTC die richtige Vorgabe."""
    if timestamp_col and "utc" in timestamp_col.lower():
        return "UTC"
    return "Europe/Berlin"
