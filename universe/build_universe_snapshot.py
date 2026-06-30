import pandas as pd
from datetime import date
from pathlib import Path

ADTV_FLOOR    = 10
MKTCAP_FLOOR  = 500
ADTV_WINDOW   = 63
PRICES_PATH   = Path("data/prices.parquet")
METADATA_PATH = Path("data/universe_metadata.parquet")
ADTV_PATH     = Path("data/adtv.parquet")
UNIVERSE_DIR  = Path("universe")
END_DATE      = date.today()

print("=" * 60)
print("Building universe snapshot : {}".format(END_DATE))
print("=" * 60)

# Load
print("\n[1/3] Loading prices and metadata...")
prices   = pd.read_parquet(PRICES_PATH)
metadata = pd.read_parquet(METADATA_PATH)
prices["date"] = pd.to_datetime(prices["date"])
print("      prices   : {} rows | {} symbols".format(prices.shape[0], prices.symbol.nunique()))
print("      metadata : {} rows".format(metadata.shape[0]))

# Compute rolling ADTV
print("\n[2/3] Computing rolling 63-day ADTV...")
prices["daily_value"] = prices["close"] * prices["volume"]

adtv_rows = []
for symbol, grp in prices.groupby("symbol"):
    grp = grp.sort_values("date").copy()
    grp["adtv_63_cr"] = (
        grp["daily_value"]
        .rolling(window=ADTV_WINDOW, min_periods=1)
        .mean() / 1e7
    ).round(2)
    adtv_rows.append(grp[["symbol", "date", "adtv_63_cr"]])

adtv = pd.concat(adtv_rows, ignore_index=True)
adtv.to_parquet(ADTV_PATH, index=False)
print("      adtv.parquet : {} rows".format(adtv.shape[0]))

# Build universe snapshot
print("\n[3/3] Building universe snapshot...")
latest_adtv = (
    adtv.sort_values("date")
    .groupby("symbol")
    .last()
    .reset_index()[["symbol", "adtv_63_cr"]]
)

universe = metadata.merge(latest_adtv, on="symbol", how="left")
universe["passes_mktcap"] = universe["market_cap_cr"] >= MKTCAP_FLOOR
universe["passes_adtv"]   = universe["adtv_63_cr"]   >= ADTV_FLOOR
universe["in_universe"]   = universe["passes_mktcap"] & universe["passes_adtv"]

UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
universe_path = UNIVERSE_DIR / "universe_{}.parquet".format(END_DATE.strftime("%Y%m%d"))
universe.to_parquet(universe_path, index=False)

print("      {} : {} rows | {} in universe".format(
    universe_path.name,
    len(universe),
    universe["in_universe"].sum()
))

print("\n" + "=" * 60)
print("SUMMARY")
print("  Total symbols    : {}".format(len(universe)))
print("  Passes mktcap    : {}".format(universe["passes_mktcap"].sum()))
print("  Passes ADTV      : {}".format(universe["passes_adtv"].sum()))
print("  In universe      : {}".format(universe["in_universe"].sum()))
print("=" * 60)
