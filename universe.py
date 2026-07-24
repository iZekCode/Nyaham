"""Scan universe: LQ45 + IDX80 + Kompas100, merged and deduplicated.

Tickers are stored WITHOUT the ``.JK`` suffix (IDX board codes). Use
:func:`to_yf` when calling yfinance.

IDX rebalances its indices roughly every 6 months (major evaluations in
February and August). These lists carry an ``EFFECTIVE_DATE`` and must be
updated manually from the official IDX index-constituent announcements.
See the "update procedure" note at the bottom of this file.
"""

from __future__ import annotations

# Effective date of the constituent lists below (YYYY-MM-DD).
# Update this whenever you refresh the lists after an IDX rebalancing.
EFFECTIVE_DATE = "2025-08-01"

# --------------------------------------------------------------------------- #
# LQ45 — 45 most liquid large caps.
# --------------------------------------------------------------------------- #
LQ45: tuple[str, ...] = (
    "ACES", "ADMR", "ADRO", "AKRA", "AMMN", "AMRT", "ANTM", "ARTO", "ASII",
    "BBCA", "BBNI", "BBRI", "BBTN", "BMRI", "BRIS", "BRPT", "CPIN", "CTRA",
    "ESSA", "EXCL", "GOTO", "ICBP", "INCO", "INDF", "INKP", "INTP", "ISAT",
    "ITMG", "JSMR", "KLBF", "MAPI", "MBMA", "MDKA", "MEDC", "PGAS", "PGEO",
    "PTBA", "SIDO", "SMGR", "SRTG", "TLKM", "TOWR", "UNTR", "UNVR", "VKTR",
)

# --------------------------------------------------------------------------- #
# IDX80 — 80 liquid names (superset of LQ45). Only the extras beyond LQ45
# are listed here; the merge below unions everything.
# --------------------------------------------------------------------------- #
IDX80_EXTRA: tuple[str, ...] = (
    "AADI", "AVIA", "BFIN", "BRMS", "BSDE", "BUKA", "CMRY", "ELSA", "EMTK",
    "ENRG", "ERAA", "GGRM", "HEAL", "HRUM", "HMSP", "INDY", "JPFA", "MAPA",
    "MIKA", "MNCN", "MTEL", "NCKL", "PANI", "PTPP", "PWON", "RAJA", "SCMA",
    "SMRA", "TINS", "TKIM", "TPIA", "WIFI", "WIKA",
)

# --------------------------------------------------------------------------- #
# Kompas100 — 100 names (superset of IDX80). Only the extras beyond
# LQ45 ∪ IDX80 are listed here.
# --------------------------------------------------------------------------- #
KOMPAS100_EXTRA: tuple[str, ...] = (
    "AGII", "AKRA", "ARNA", "AUTO", "BBHI", "BBKP", "BDMN", "BJBR", "BJTM",
    "BNGA", "BSSR", "BTPS", "DEWA", "DSNG", "FILM", "INDLK", "IPCC", "ITMA",
    "KAEF", "LSIP", "MARK", "MDLN", "MYOR", "PNBN", "PNLF", "PSAB", "RALS",
    "SMDR", "SSIA", "SSMS", "TBIG", "TOBA", "ULTJ", "WOOD", "WTON",
)


def _clean(sym: str) -> str:
    """Uppercase, drop any ``.JK`` suffix, strip whitespace."""
    return sym.strip().upper().removesuffix(".JK")


def normalize(ticker: str) -> str:
    """Normalize any user input to a bare IDX board code (e.g. ``ARCI``)."""
    return _clean(ticker)


def to_yf(ticker: str) -> str:
    """Return the yfinance symbol (append ``.JK``)."""
    return f"{normalize(ticker)}.JK"


def get_universe() -> list[str]:
    """Merged, deduplicated, sorted list of bare board codes."""
    merged = {
        _clean(t)
        for group in (LQ45, IDX80_EXTRA, KOMPAS100_EXTRA)
        for t in group
    }
    return sorted(merged)


# Precomputed for convenience.
UNIVERSE: list[str] = get_universe()


if __name__ == "__main__":
    u = get_universe()
    print(f"Effective date: {EFFECTIVE_DATE}")
    print(f"Universe size : {len(u)} tickers")
    print(", ".join(u))

# --------------------------------------------------------------------------- #
# UPDATE PROCEDURE (run after each IDX rebalancing, ~Feb & Aug):
#   1. Open the official IDX index fact sheets / constituent announcements:
#        https://www.idx.co.id  →  Products  →  Index
#      (LQ45, IDX80, KOMPAS100 constituent PDFs).
#   2. Replace LQ45 with the full 45-name list. For IDX80_EXTRA and
#      KOMPAS100_EXTRA, list only the names NOT already in the lower tier
#      (keeps the file free of duplicates; the merge unions them anyway).
#   3. Bump EFFECTIVE_DATE.
#   4. Run `python universe.py` to sanity-check the size (~100–120).
# --------------------------------------------------------------------------- #
