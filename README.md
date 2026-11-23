# BTC 1m bot: trend-return rider with a one-loss budget

[![make.fun · btc-race-24h-20261123-0511](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)

A BTC trading bot for make.fun's "Race a BTC trading bot live for 24 hours"
($50 pool). One program, `main.py` (Python 3.13, standard library only, no
network), speaks agent-fun/bars-replay/v1: one batch per minute of cbBTC
bars, one reply per batch.

## Strategy

One entry mode, one position, long only, full-equity sizing:

- **Entry** — buy when the latest close sits at least 50 bps above the
  close 120 minutes ago (a 2-hour trend return). In a slow grind this
  fires far earlier than a new-high breakout (which needs a sharp pop),
  so the bot is invested for most of the move; in a flat market the
  120-minute return hovers near zero and the bot mostly stands aside.
- **Exit** — no take profit (the bot rides the trend to the end of the
  window); stop loss -300 bps from cost; trailing stop -225 bps from the
  position's peak value; max hold 1380 bars (safety rail only).

Risk controls: 5-bar cooldown after any sell, no new entries in the final
hour, a 10% equity circuit breaker, and a **one-loss budget** — after one
round trip closed at a loss the bot stops opening new positions for the
rest of the window. That single rule is what keeps flat/down windows from
bleeding the bot dry through repeated false signals. Every order caps
slippage at 150 bps. All decisions use exact integer arithmetic (no
floats in the decision path), so the bot is exactly reproducible.

## Results

Dev window (24h, 1,440 bars, 22–23 Nov 2026; +3.64%, 1-minute volatility
0.081%): **+$31.09 net on 2 trades (1 round trip: bought bar 127, held
to the flatten), max drawdown 1.98%, fees $0.81**, against +$33.88 for
buy-and-hold. Verified with a faithful protocol replay of the real
`main.py` (two runs, identical replies = deterministic, 0 rejected
orders).

Robustness on three other real 24h dev windows (same parameters, no
re-tuning per window): 16–17 Nov (+0.49%) -$2.90; 14–15 Nov (-4.00%)
-$19.12; 17–18 Nov (-2.80%) -$7.79 — one round trip each, then stopped
by the one-loss budget. On 200 block-bootstrap resamples of this
window's returns (240-minute blocks): median +$22.04, 75% positive.

The three earlier bots all lose on this window and were rejected: the
15 Nov breakout+dip bot -$16.17 (14 trades, whipsawed by tight exits in
the grind), the 17/18 Nov regime-dip bots -$4.45 (8 trades,
mean-reversion exits sell the trend too early, then re-buy higher).

A full round trip costs ~26–35 bps on this window (4 bps fee + 2 bps
slippage + ~7–16 bps of pool impact at full size), so the bot only
trades when a sustained move is under way.
