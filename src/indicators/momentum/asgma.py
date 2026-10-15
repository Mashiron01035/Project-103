"""

    -*- coding: utf-8 -*-
    File        : asgma.py
    Project     : Project-103
    Description : ALMA Smoothed Gaussian Moving Average (ASGMA).

                  Ported from TradingView Pine Script (© profitprotrading).
                  Optimized with numpy for fast backtesting.

                  Combines:
                  1. ALMA-smoothed percentage price change (momentum speed)
                  2. Gaussian Adaptive Moving Average of that speed (adaptive threshold)
                  3. RSI(14) direction confirmation
                  4. Chande Momentum Oscillator(9) direction confirmation

                  Outputs:
                  - avpchange: smoothed momentum speed
                  - gma: adaptive threshold
                  - signal: +1 (bullish cross), -1 (bearish cross), 0 (none)
                  - confirmed: +1/-1 only when RSI + Chande agree
                  - direction: +1 (bullish) / -1 (bearish)

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

import numpy as np

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType


def _alma_np(arr: np.ndarray, length: int, offset: float = 0.85, sigma: float = 7.0) -> np.ndarray:
    """Vectorized ALMA over full array. Returns array same length as input."""
    n = len(arr)
    out = np.full(n, np.nan)
    if n < length:
        return out

    m = offset * (length - 1)
    s = length / sigma
    idx = np.arange(length, dtype=np.float64)
    weights = np.exp(-((idx - m) ** 2) / (2 * s * s))
    norm = weights.sum()

    # Convolution — equivalent to rolling weighted average
    for i in range(length - 1, n):
        out[i] = np.dot(arr[i - length + 1 : i + 1], weights) / norm

    return out


def _ema_np(arr: np.ndarray, period: int) -> np.ndarray:
    """EMA over numpy array, returns full series."""
    out = np.empty_like(arr)
    k = 2.0 / (period + 1)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi_np(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI over numpy array, returns full series."""
    n = len(closes)
    rsi = np.full(n, 50.0)
    if n < period + 1:
        return rsi

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rsi[i + 1] = 100 - (100 / (1 + avg_gain / avg_loss))

    return rsi


def _chande_np(closes: np.ndarray, period: int = 9) -> np.ndarray:
    """Chande Momentum Oscillator over numpy array."""
    n = len(closes)
    cmo = np.zeros(n)
    if n < period + 1:
        return cmo

    changes = np.diff(closes)
    ups = np.where(changes > 0, changes, 0.0)
    downs = np.where(changes < 0, -changes, 0.0)

    for i in range(period, len(changes)):
        sm1 = ups[i - period + 1 : i + 1].sum()
        sm2 = downs[i - period + 1 : i + 1].sum()
        total = sm1 + sm2
        if total > 0:
            cmo[i + 1] = 100 * (sm1 - sm2) / total

    return cmo


def _running_max(arr: np.ndarray, length: int) -> np.ndarray:
    """Rolling max over array."""
    n = len(arr)
    out = np.empty(n)
    for i in range(n):
        start = max(0, i - length + 1)
        out[i] = arr[start : i + 1].max()
    return out


def _running_min(arr: np.ndarray, length: int) -> np.ndarray:
    """Rolling min over array."""
    n = len(arr)
    out = np.empty(n)
    for i in range(n):
        start = max(0, i - length + 1)
        out[i] = arr[start : i + 1].min()
    return out


class ASGMA(BaseIndicator):
    """
    ALMA Smoothed Gaussian Moving Average (numpy-optimized).

    Parameters: asgma{smooth,alma_length,gma_length,vol_period}
    Defaults:   asgma{1,25,14,20}
    """

    def __init__(self, name: str = "asgma"):
        super().__init__(name)

    def calculate(
        self,
        candles: List[Candle],
        smooth: int = 1,
        alma_length: int = 25,
        gma_length: int = 14,
        vol_period: int = 20,
    ) -> IndicatorResult:
        min_needed = max(alma_length, gma_length, vol_period, 14, 9) + smooth + 10
        self.validate_candles(candles, min_needed)

        closes = np.array(self.get_closes(candles), dtype=np.float64)
        n = len(closes)

        # --- Step 1: ALMA-smoothed percentage change ---
        pchange = np.zeros(n)
        pchange[smooth:] = (closes[smooth:] - closes[:-smooth]) / closes[:-smooth] * 100

        avpchange_arr = _alma_np(pchange, alma_length, 0.85, 7.0)
        # Fill NaN at start
        avpchange_arr = np.nan_to_num(avpchange_arr, nan=0.0)

        avpchange = avpchange_arr[-1]
        prev_avpchange = avpchange_arr[-2] if n >= 2 else 0.0

        # --- Step 2: Gaussian Adaptive Moving Average ---
        # Pre-compute rolling max/min of avpchange for each lookback 1..gma_length
        # gma = sum(weight_i * (highest(avp, i+1) + lowest(avp, i+1))) / sum(weights) / 2
        # Then EMA(gma_series, 7)

        # Compute sigma = stdev(close, vol_period) as rolling series
        # For efficiency, compute only for last ~60 points (enough for EMA warmup)
        ema_warmup = 60
        start_idx = max(0, n - ema_warmup)

        gma_raw = np.zeros(ema_warmup)

        for step in range(ema_warmup):
            idx = start_idx + step
            if idx < gma_length + vol_period:
                continue

            # Adaptive sigma from rolling stdev
            subset = closes[max(0, idx - vol_period + 1) : idx + 1]
            sigma = float(np.std(subset))
            if sigma < 0.01:
                sigma = 0.01

            g = 0.0
            sw = 0.0
            for j in range(gma_length):
                w = np.exp(-(((j - (gma_length - 1)) / (2 * sigma)) ** 2) / 2)
                lb = max(0, idx - j)
                seg = avpchange_arr[lb : idx + 1]
                if len(seg) == 0:
                    continue
                g += (float(seg.max()) + float(seg.min())) * w
                sw += w

            gma_raw[step] = (g / sw / 2) if sw > 0 else 0.0

        gma_ema = _ema_np(gma_raw, 7)
        gma_final = float(gma_ema[-1])
        prev_gma = float(gma_ema[-2]) if len(gma_ema) >= 2 else gma_final

        # --- Step 3: Signals ---
        bullish_cross = prev_avpchange < prev_gma and avpchange >= gma_final
        bearish_cross = prev_avpchange > prev_gma and avpchange <= gma_final

        direction = 1.0 if avpchange >= gma_final else -1.0

        signal = 0.0
        if bullish_cross:
            signal = 1.0
        elif bearish_cross:
            signal = -1.0

        # --- Step 4: RSI confirmation ---
        rsi_arr = _rsi_np(closes, 14)
        rsi = float(rsi_arr[-1])
        rsi_prev = float(rsi_arr[-2]) if n >= 2 else rsi
        rsi_rising = rsi > rsi_prev
        rsi_falling = rsi < rsi_prev

        # --- Step 5: Chande Momentum ---
        cmo_arr = _chande_np(closes, 9)
        cmo = float(cmo_arr[-1])
        cmo_prev = float(cmo_arr[-2]) if n >= 2 else cmo
        cmo_rising = cmo > cmo_prev
        cmo_falling = cmo < cmo_prev

        # Confirmed signal
        confirmed = 0.0
        if signal == 1.0 and rsi_rising and cmo_rising:
            confirmed = 1.0
        elif signal == -1.0 and rsi_falling and cmo_falling:
            confirmed = -1.0

        signals = []
        if confirmed == 1.0:
            signals.append(SignalType.BULLISH_CROSSOVER.value)
        elif confirmed == -1.0:
            signals.append(SignalType.BEARISH_CROSSOVER.value)
        if direction == 1.0:
            signals.append(SignalType.BULLISH.value)
        else:
            signals.append(SignalType.BEARISH.value)

        return IndicatorResult(
            name=self.name,
            timestamp=candles[-1].timestamp,
            values={
                "avpchange": round(float(avpchange), 6),
                "gma": round(gma_final, 6),
                "signal": signal,
                "confirmed": confirmed,
                "direction": direction,
                "rsi": round(rsi, 2),
                "cmo": round(cmo, 2),
            },
            signals=signals,
            metadata={
                "smooth": smooth,
                "alma_length": alma_length,
                "gma_length": gma_length,
                "vol_period": vol_period,
            },
        )
