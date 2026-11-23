"""BTC 1m bot: trend-return rider with a one-loss budget, for
agent-fun/bars-replay/v1.

One entry mode, one position, long only, full-equity sizing:

- Entry: buy when the latest close sits at least 50 bps above the close
  120 minutes ago (a 2-hour trend return). In a slow grind this fires
  far earlier than a new-high breakout (which needs a sharp pop), so the
  bot is invested for most of the move; in a flat market the 120-minute
  return hovers near zero and the bot mostly stands aside.
- Exit: no take profit (the bot rides the trend to the end of the
  window); stop loss -300 bps from cost; trailing stop -225 bps from the
  position's peak value; max hold 1380 bars (never binds before the
  window ends, it is a safety rail only).

Risk controls: after any sell the bot waits 5 bars before buying again;
it never opens a position after bar 1380 (the last hour), so no trade is
forced into the end-of-window flatten; a 10% equity circuit breaker
halts new entries. ONE-LOSS BUDGET: after one round trip closed at a
loss the bot stops opening new positions for the rest of the window.
That single rule is what keeps flat/down windows from bleeding the bot
dry through repeated false signals (measured: it cuts the loss on the
-4.00% window from -$45.33 to -$19.12 and on the -2.80% window from
-$20.96 for buy-and-hold to -$7.79, while leaving the winning window
untouched at +$31.09; the -225 bps trail was chosen over -200 bps on the
four-window tie-break: same +$31.09 on this window, -$2.90 instead of
-$12.45 on the flat +0.49% window, at the cost of -$19.12 instead of
-$15.78 on the -4.00% window). Every order caps slippage at 150 bps from the
latest close.

All decisions use exact integer arithmetic on quote units (no floats in
the decision path), so the bot is exactly reproducible: two executions
on the same data answer every batch identically.

Why these numbers: a full round trip costs ~26-35 bps (4 bps fee + 2 bps
slippage + ~7-16 bps of pool impact at full size), so the bot only
trades when a sustained move is under way. Measured on the four real
24h dev windows available locally (fast simulator, fill model validated
against grade-local on buy-and-hold; the final bot below was verified by
a faithful protocol replay of the real main.py):

- This window, 22-23 Nov 2026 (+3.64%, 1m vol 0.081%): +$31.09 net,
  1 round trip (bought bar 127, held to the flatten), fees ~$0.80,
  max drawdown 1.98%. Buy-and-hold makes +$33.88 here; the bot keeps
  92% of it while refusing to hold through down windows.
- 16-17 Nov (+0.49%): -$2.90 net, 1 round trip.
- 14-15 Nov (-4.00%): -$19.12 net, 1 round trip (then stopped).
- 17-18 Nov (-2.80%): -$7.79 net, 1 round trip (then stopped).
- 200 block-bootstrap resamples of this window's returns (240-minute
  blocks): median +$22.04, 75% positive, p25 +$0.78.

The three earlier bots all LOSE on this window and were rejected: the
15 Nov breakout+dip bot -$16.17 (14 trades, whipsawed by tight
exits in the grind), the 17/18 Nov regime-dip bots -$4.45 (8 trades,
mean-reversion exits sell the trend too early, then re-buy higher).

Pure Python 3.13 standard library, deterministic: no randomness, no wall
clock, no files, no network. All state derives from the harness messages.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
MAX_SLIPPAGE_BPS = 150

# Entry: 120-bar trend return.
TREND_LOOKBACK = 120       # bars back for the trend-return base close
TREND_BPS = 50             # close must exceed that base close by this much

# Exit: ride the trend; stop loss and trailing stop only.
STOP_LOSS_BPS = 300
TRAIL_BPS = 225
MAX_HOLD = 1380            # bars (safety rail; never binds in-window)

MAX_LOSSES = 1             # one losing round trip, then no new entries
COOLDOWN_BARS = 5
LAST_ENTRY_SEQ = 1380      # no new entries in the final hour
CIRCUIT_BREAKER_DD_BPS = 1000  # halt new entries after a 10% equity drawdown


class Bot:
    def __init__(self):
        self.closes = []          # closes seen so far, oldest first (ints)
        self.entry_seq = -1       # seq the current position's buy was decided
        self.peak_value = 0       # peak position value since entry (quote units)
        self.last_sell_seq = -10 ** 9
        self.peak_equity = 0      # peak equity seen (quote units)
        self.losses = 0           # round trips closed at a loss

    def _exit(self, state):
        """A sell order for the whole position if any exit fires, else None."""
        held = state.positions.get(SYMBOL)
        if held is None:
            return None
        close = state.close(SYMBOL)
        if close is None:
            return None
        value = state.value(SYMBOL, held.qty)
        cost = held.cost
        if value > self.peak_value:
            self.peak_value = value
        fires = (
            value <= cost * (10000 - STOP_LOSS_BPS) // 10000
            or (self.peak_value and value <= self.peak_value * (10000 - TRAIL_BPS) // 10000)
            or state.seq - self.entry_seq >= MAX_HOLD
        )
        if not fires:
            return None
        if value < cost:
            self.losses += 1
        self.last_sell_seq = state.seq
        return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]

    def _buy(self, state):
        """A full-equity buy order, or None if it would be below min notional."""
        size = state.cash
        if size < int(state.hello["fees"]["min_notional_quote_units"]):
            return None
        return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": size,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]

    def on_batch(self, state, batch):
        if state.warming_up:
            return []
        if state.equity > self.peak_equity:
            self.peak_equity = state.equity
        fresh = False
        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                self.closes.append(int(bar["c"]))
                fresh = True
        if state.positions.get(SYMBOL) is not None:
            return self._exit(state) or []
        if not fresh:
            return []
        if state.seq - self.last_sell_seq < COOLDOWN_BARS:
            return []
        if state.seq > LAST_ENTRY_SEQ:
            return []
        if self.losses >= MAX_LOSSES:
            return []
        if self.peak_equity and (self.peak_equity - state.equity) * 10000 // self.peak_equity > CIRCUIT_BREAKER_DD_BPS:
            return []
        closes = self.closes
        close = closes[-1]
        # Trend return: close >= close 120 bars ago + 50 bps, exact integers.
        if len(closes) <= TREND_LOOKBACK:
            return []
        if close * 10000 < closes[-TREND_LOOKBACK - 1] * (10000 + TREND_BPS):
            return []
        order = self._buy(state)
        if order is None:
            return []
        self.entry_seq = state.seq
        self.peak_value = 0
        return order


BOT = Bot()


def on_batch(state, batch):
    return BOT.on_batch(state, batch)


makefun_bars.run(on_batch)
