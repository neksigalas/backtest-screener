"""
ETF universe for 3 Best ETF Paper Trading.
Focus: leveraged (2x/3x) ETFs + volatile sector ETFs so ±10% TP/SL triggers fast.
"""

# Leverage multiplier per ticker (unlisted = 1x)
LEVERAGE: dict[str, int] = {
    # ── 3x leveraged ────────────────────────────────────────────────────────
    "TQQQ": 3, "SOXL": 3, "LABU": 3, "TECL": 3, "NAIL": 3, "CURE": 3,
    "DPST": 3, "FAS":  3, "ERX":  3, "GUSH": 3, "SPXL": 3, "UPRO": 3,
    "UDOW": 3, "TNA":  3, "MIDU": 3, "DFEN": 3, "RETL": 3, "WANT": 3,
    # ── 2x leveraged ────────────────────────────────────────────────────────
    "QLD":  2, "SSO":  2, "DDM":  2, "ROM":  2, "UYG":  2, "UWM":  2,
    "BOIL": 2, "UCO":  2, "MVV":  2, "UYM":  2,
}

# Full universe ordered by preference (leveraged first)
ETF_UNIVERSE: list[str] = [
    # 3x Leveraged
    "TQQQ", "SOXL", "LABU", "TECL", "NAIL", "CURE", "DPST", "FAS",
    "ERX",  "GUSH", "SPXL", "UPRO", "UDOW", "TNA",  "MIDU", "DFEN",
    "RETL", "WANT",
    # 2x Leveraged
    "QLD",  "SSO",  "DDM",  "ROM",  "UYG",  "UWM",  "BOIL", "UCO",
    "MVV",  "UYM",
    # Volatile sector ETFs (1x but high beta)
    "SMH",  "SOXX", "ARKK", "IBB",  "GDX",  "GDXJ", "XBI",  "SILJ",
    "KRE",  "JETS", "XOP",  "ITB",  "KWEB", "ARKG", "CIBR", "AIQ",
    "BOTZ", "HACK", "XHB",  "ARKS",
]
