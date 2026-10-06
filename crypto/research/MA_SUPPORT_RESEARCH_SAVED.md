# 4H MA Support Research — Saved State (2026-10-05)

## Chart interpretation

The TradingView screenshots use these dynamic 4H price rails:

- **MA111** — cyan.
- **SMA350** — orange/base line.
- **SMA350 × 2** — green.
- **SMA350 × 3** — red.
- **SMA350 × 5** — purple.
- **SMA350 × 8** — blue.

Important: the Golden Ratio labels are **multiples of the current SMA350 price value**, not SMA periods 700/1050/1750/2800.

The separate SMA350 indicator shown in the screenshots uses:
- Length 350
- Source Close
- Offset 0
- Smoothing method SMA
- Smoothing length 5
- Chart timeframe
- Wait for timeframe closes

The SMA-5 smoothing is context only unless a research rule explicitly uses it.

## Research already completed

### Pass 1 — MA111 / SMA350 breakout -> first retest

The strongest MA111 touch variants were promising but did not clear the full promotion gate.

Representative later-40% / frozen external results:
- MA111, prior 90% below, breakout above, >=8% extension, first touch within 12 bars, rising MA:
  - Hyperliquid +5% hit about 76.7%
  - target-before-7.5% stop about 73.3%
  - modeled net ROI about +1.44%/trade
  - Binance +5% hit about 72.2%
  - modeled net ROI about +1.31%/trade
- A slightly broader MA111 variant reached about +1.58% HL ROI and +1.48% Binance ROI.

SMA350 by itself was less stable and did not show enough train/holdout consistency.

### CHIP August 2026 diagnostic

The scanner found the user-supplied CHIP example:
- MA111 breakout around 2026-08-15.
- First MA111 touch closed about 1.8% below the line.
- A touch-style entry subsequently had roughly +35.8% 5-day MFE with about -2.2% adverse excursion.
- +5%, +7.5%, +10%, and +15% were all reached before the modeled -7.5% stop.

This showed that requiring the exact touch candle to close above MA111 can reject good trades.

### Pass 2 — allow basing around support + RSI confirmation

The only RSI family that repeatedly looked useful was a **local RSI bottom/turn** around support.

Interesting later-period MA111 examples:
- rising MA + >=8% extension + RSI bottom-turn within 4 bars:
  - HL holdout roughly 83–85% +5%
  - HL modeled ROI roughly +2.3%
  - Binance roughly 72–75% +5%
  - Binance ROI roughly +1.7% to +2.5%

However, the earlier Hyperliquid training period was materially weaker, so these variants were **not promoted**.

Simple filters such as RSI merely rising for two bars, RSI above SMA3/SMA5, or +3/+5 point RSI recovery did not generalize well enough.

## Current interpretation

The strongest idea worth continuing is:

**long period below a major rail -> breakout -> meaningful extension -> first support/reclaim -> MA111 / SMA350 structure remains constructive -> RSI is holding a higher local floor or turning from a bottom -> rebound.**

The next research lane extends this to the full Golden Ratio rail system:
SMA350, SMA350×2, SMA350×3, SMA350×5, SMA350×8, plus MA111 interactions.

No MA-support method is live yet.
