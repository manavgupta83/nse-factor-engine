# NSE Factor Engine — Known Issues

---

## [KI-001] VEDL — Missing Stock Split Adjustment in Price Data

**Status:** Open  
**Stage affected:** Stage 2 (Momentum Signal — Core)  
**Discovered:** During stage2_step3_vol.py violation investigation  
**Date logged:** 2026-06-29  

**Description:**  
VEDL (Vedanta Ltd) underwent a stock split on 30 Apr 2026. The split adjustment
is not reflected in `data/prices.parquet` — the raw price series shows a large
single-day drop on that date which is a corporate action, not a real price move.

**Impact:**  
- `vol_252` and `downside_vol_252` for VEDL are artificially inflated (~1.10 and ~1.58)
- `vol_adj_ret` and `sharpe_style` and `sortino_style` will be deflated (divided by inflated vol)
- VEDL will rank poorly on vol-adjusted signals despite potentially valid raw momentum
- Flagged as one of 4 `downside_vol > total_vol` violations in step 3 sanity check

**Root cause:**  
yfinance does not carry the correct split-adjusted series for VEDL as of this date.
No reliable automated fix available at source.

**Decision:**  
Leave as-is for now. Signal values are mathematically valid but economically
misleading for VEDL specifically. Do not exclude from output — NaN would hide
the issue. Instead, add a `data_quality_flag` column in the signals output
to mark affected symbols.

**Resolution path:**  
- Source correct split-adjusted VEDL prices from NSE bhavcopies or a paid data vendor
- Repatch `data/prices.parquet` for VEDL post-split dates
- Re-run Stage 2 after patch and verify vol_252 returns to expected range (~0.3–0.5)

**Related:**  
- stage2_investigate_violations.py — full diagnostic output
- Symbols also showing downside_vol > total_vol: TMPV, TRENT, IEX (legitimate edge case, not data errors)

---

## [KI-002] VEDL — Volume Baseline Distorted by Unadjusted Stock Split

**Status:** Open  
**Stage affected:** Stage 4 (Entry Quality Filters — Volume Confirmation)  
**Discovered:** During stage4_step2 volume confirmation sanity check  
**Date logged:** 2026-06-29  

**Description:**  
VEDL underwent a stock split on 30 Apr 2026 (same corporate action as KI-001).
The split is reflected as a mechanical ~3x jump in raw share volume — more
shares change hands post-split for the same rupee value traded, since each
share is worth roughly 1/3 as much. `data/prices.parquet` carries this
discontinuity uncorrected, the same way it carries the price discontinuity.

**Evidence:**  
- Pre-split (2025-08 to 2026-04-29, n=266 days): mean volume ~12.1M shares/day
- Post-split (2026-04-30 to T, n=39 days): mean volume ~36.7M shares/day
- Ratio: 3.03x post/pre — matches the price-side split ratio (close: ₹773.60 → ₹271.55, ~2.85x) closely enough to confirm common cause
- Volume jump is sharp and coincides exactly with the price discontinuity date (2026-04-30)

**Impact:**  
- `vol_ratio_21_252` (Stage 4, `metrics/volume_confirmation.py`) averages
  `avg_vol_252` over a window that spans both volume regimes (266 pre-split
  days at the old share-count scale, 39 post-split days at ~3x scale as of
  T). The resulting 252d baseline is not a stable, single-regime average —
  it is a blend across a structural break.
- Observed: VEDL `vol_ratio_21_252` = 1.71, `volume_price_pos_move_confirmed`
  = False (price leg is negative, so the flag is unaffected by this issue
  in the current snapshot — but the ratio itself is not reliably
  interpretable, and a future date could see this issue produce a false
  positive).
- As the 252d window keeps rolling forward, the pre-split tail will
  eventually fully drop out and the ratio will self-correct — but until
  then (~213 more trading days from T), `vol_ratio_21_252` for VEDL is
  unreliable.

**Root cause:**  
Same as KI-001 — yfinance does not carry a split-adjusted series for VEDL.
Price and volume are both affected, as expected of a true split with no
corresponding adjustment applied upstream.

**Decision:**  
Leave as-is for now, consistent with KI-001's resolution. Do not exclude
VEDL from Stage 4 output — flag via `data_quality_flag` (existing column,
already marks VEDL from Stage 2) rather than NaN, so the issue is visible
rather than silently hidden.

**Resolution path:**  
- Shared with KI-001 — source correct split-adjusted VEDL OHLCV (price AND
  volume) from NSE bhavcopies or a paid data vendor
- Repatch `data/prices.parquet` for VEDL across the full series (not just
  post-split dates, depending on vendor convention — confirm which side
  the vendor adjusts)
- Re-run Stage 2 and Stage 4 after patch; verify `vol_ratio_21_252` for
  VEDL returns to a range consistent with other large-cap, non-split
  symbols (roughly 0.5–2.0 based on current cross-sectional distribution)

**Related:**  
- KI-001 — same corporate action, price/realised-vol side of the issue
- stage4_step2_test_volconf.py, stage4_vedl_volume_check.py — diagnostic scripts

---
