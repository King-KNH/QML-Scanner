"""Scanner configuration: watched assets (names as shown in the Deriv app) and scan parameters.

Names are display names. The exact Deriv symbol codes are resolved at runtime from the broker's own
symbol list (the data layer), so they are deliberately not hard-coded here.
Removed on request: Range Break indices.
"""
from .candles import TF_ORDER

FOREX = ["AUDJPY", "AUDUSD", "EURAUD", "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURUSD",
         "GBPAUD", "GBPJPY", "GBPUSD", "USDCAD", "USDCHF", "USDJPY"]
METALS_CRYPTO = ["XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD"]
STEP = ["Step Index", "Step Index 200", "Step Index 300", "Step Index 400", "Step Index 500"]
VOLATILITY_BOTH = [5, 10, 15, 25, 30, 50, 75, 90, 100]      # 2s and 1s versions
VOLATILITY_1S_ONLY = [150, 250]
VOLATILITY = (["Volatility %d Index" % v for v in VOLATILITY_BOTH]
              + ["Volatility %d (1s) Index" % v for v in VOLATILITY_BOTH + VOLATILITY_1S_ONLY])
JUMP = ["Jump %d Index" % v for v in (10, 25, 50, 75, 100)]
BOOM_CRASH_N = [50, 99, 100, 150, 200, 300, 500, 600, 900, 1000]
BOOM = ["Boom %d Index" % n for n in BOOM_CRASH_N]
CRASH = ["Crash %d Index" % n for n in BOOM_CRASH_N]

ASSETS = {
    "Forex": FOREX,
    "Métaux & crypto": METALS_CRYPTO,
    "Step": STEP,
    "Volatility": VOLATILITY,
    "Jump": JUMP,
    "Boom": BOOM,
    "Crash": CRASH,
}
ALL_SYMBOLS = [s for group in ASSETS.values() for s in group]

# M5 is only the stored BASE series (everything is derived from it). It is NOT scanned: too many setups, too much noise.
TIMEFRAMES = [tf for tf in TF_ORDER if tf != "M5"]    # M15, M30, M45, H1, H2, H4, H8, D1
BASE_TF = "M5"
BASE_SEC = 300
WINDOW = 500                         # closed candles analysed per timeframe
APPROACH_ATR = 0.5                   # "very close" = within 0.5 ATR of the QML line
CLUSTER_TOL_ATR = 0.25               # overlap tolerance when merging timeframes
MAX_PINNED = 40


CATEGORY_ORDER = list(ASSETS)
CATEGORY_OF = {sym: cat for cat, syms in ASSETS.items() for sym in syms}
SYMBOL_ORDER = {sym: i for i, sym in enumerate(ALL_SYMBOLS)}


def category_of(symbol: str) -> str:
    return CATEGORY_OF.get(symbol, "Autres")
