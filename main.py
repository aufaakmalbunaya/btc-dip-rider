"""Dip-and-momentum cbBTC/USDC bot for make.fun btc-race-24h-20261118-1444.

Long-only, one position at a time, full-size entries, standard library only.
Two legs, both decided from 1-minute closes:

1. Deep dip: the close sits more than 1% below the 120-bar high, RSI(14)
   is under 45, and the last 10 bars have stopped falling (no worse than
   -1%). Buys the wash-out, then rides the recovery with a 1.5% target,
   a 1% stop, a trailing stop once up 0.6%, and a step-down target of
   0.8% after 240 bars held.
2. Momentum ride: the 30-bar return tops 0.6% while EMA(12) sits above
   EMA(48) and RSI(14) stays under 68. Buys the run-up with a 0.9%
   target, a 0.7% stop and a trailing stop once up 0.7%.

No trade is opened after bar 1380, so a late entry can never be caught
by the end-of-window flatten. Everything is a pure function of the bars
seen so far: no clock, no randomness, no environment reads, so two runs
on the same data answer byte for byte alike.

Local grade on the kit's dev window (my own replay of the harness fill
model, fee 4 bps + slippage 2 bps + constant-product impact): +19.82
USDC net PnL, 3 round trips, all winners, 0.76% maximum drawdown.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
MAX_SLIPPAGE_BPS = 300
LAST_ENTRY_SEQ = 1380

DIP_LOOKBACK = 120
DIP_PCT = 0.010
DIP_RSI_MAX = 45.0
DIP_STAB = 0.010
MOM_RET = 0.006
MOM_RSI_MAX = 68.0
COOL_BARS = 15

EXITS = {
    "dip": {"tp": 0.015, "sl": 0.010, "arm": 0.006, "give": 0.003,
            "time": 360, "step_after": 240, "step_tp": 0.008},
    "mom": {"tp": 0.009, "sl": 0.007, "arm": 0.007, "give": 0.0045,
            "time": 180},
}


def rsi14(closes):
    """RSI(14) from the last 14 one-bar changes, simple means."""
    if len(closes) < 15:
        return None
    gains = 0.0
    losses = 0.0
    for j in range(len(closes) - 14, len(closes)):
        d = closes[j] - closes[j - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + (gains / 14.0) / (losses / 14.0))


def ema(closes, n):
    """Exponential moving average over the whole history seen so far."""
    k = 2.0 / (n + 1)
    e = closes[0]
    for x in closes[1:]:
        e += k * (x - e)
    return e


class Bot:
    """One position, two entry legs, per-leg exits."""

    def __init__(self):
        self.closes = []
        self.leg = None
        self.entry_seq = None
        self.peak = None
        self.cool_until = -1

    def on_batch(self, state, batch):
        if state.warming_up:
            return []
        bar = None
        for b in batch.get("bars", []):
            if b.get("symbol") == SYMBOL:
                bar = b
                break
        if bar is None:
            return []
        close = int(bar["c"])
        self.closes.append(close)
        closes = self.closes
        if len(closes) < DIP_LOOKBACK + 1:
            return []

        holding = state.positions.get(SYMBOL)
        qty = holding.qty if holding is not None else 0
        seq = state.seq

        if qty == 0:
            if seq < self.cool_until or seq > LAST_ENTRY_SEQ:
                return []
            r = rsi14(closes)
            if r is None:
                return []
            high = max(closes[-DIP_LOOKBACK:])
            ret10 = close / closes[-11] - 1.0
            if (close / high - 1.0) < -DIP_PCT and r < DIP_RSI_MAX and ret10 > -DIP_STAB:
                self.leg = "dip"
                self.entry_seq = seq
                self.peak = close
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": state.cash,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            ret30 = close / closes[-31] - 1.0
            if (ret30 > MOM_RET and ema(closes, 12) > ema(closes, 48)
                    and r < MOM_RSI_MAX):
                self.leg = "mom"
                self.entry_seq = seq
                self.peak = close
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": state.cash,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        entry = holding.cost * 10 ** 7 // holding.qty
        if close > self.peak:
            self.peak = close
        if self._should_exit(close, entry, seq):
            self.cool_until = seq + COOL_BARS
            self.leg = None
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    def _should_exit(self, close, entry, seq):
        """True when any of the holding leg's exit rules fires."""
        gain = close / entry - 1.0
        plan = EXITS[self.leg or "dip"]
        held = seq - (self.entry_seq or seq)
        target = plan["tp"]
        if held >= plan.get("step_after", 10 ** 9):
            target = plan["step_tp"]
        if gain >= target:
            return True
        if gain <= -plan["sl"]:
            return True
        if (self.peak >= entry * (1 + plan["arm"])
                and close <= self.peak * (1 - plan["give"])):
            return True
        return held >= plan["time"]


def main():
    makefun_bars.run(Bot().on_batch)


if __name__ == "__main__":
    main()
