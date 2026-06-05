"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  AreaSeries,
  CrosshairMode,
} from "lightweight-charts";
import type { PriceBar } from "@/lib/api";
import { sma, ema, rsi, macd, bollingerBands } from "@/lib/indicators";

import type { Indicators } from "@/lib/api";

interface Props {
  prices: PriceBar[];
  ticker: string;
  indicators: Indicators;
}

const THEME = {
  bg:      "#0f172a",  // slate-900
  text:    "#94a3b8",  // slate-400
  grid:    "#1e293b",  // slate-800
  border:  "#334155",  // slate-700
  up:      "#22c55e",  // green-500
  down:    "#ef4444",  // red-500
  vol:     "#475569",  // slate-600
  ma20:    "#60a5fa",  // blue-400
  ma50:    "#f59e0b",  // amber-400
  ma200:   "#a78bfa",  // violet-400
  bbUpper: "#94a3b8",
  bbLower: "#94a3b8",
  bbFill:  "rgba(148,163,184,0.08)",
  rsiLine: "#f59e0b",
  macdLine:"#60a5fa",
  signal:  "#f43f5e",
  histUp:  "#22c55e",
  histDn:  "#ef4444",
};

export function TradingChart({ prices, ticker, indicators }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<ReturnType<typeof createChart> | null>(null);

  useEffect(() => {
    if (!containerRef.current || prices.length === 0) return;

    const el = containerRef.current;

    // Determine which sub-panes to create
    const showRsi  = indicators.rsi;
    const showMacd = indicators.macd;
    const MAIN_H   = showRsi || showMacd ? 340 : 460;
    const SUB_H    = 110;
    const totalH   = MAIN_H + (showRsi ? SUB_H : 0) + (showMacd ? SUB_H : 0);

    const chart = createChart(el, {
      width:  el.clientWidth,
      height: totalH,
      layout: {
        background: { color: THEME.bg },
        textColor:  THEME.text,
        fontSize:   11,
      },
      grid: {
        vertLines: { color: THEME.grid },
        horzLines: { color: THEME.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: THEME.border },
      timeScale: {
        borderColor: THEME.border,
        timeVisible: true,
        secondsVisible: false,
      },
    });
    chartRef.current = chart;

    const mainPane = chart.panes()[0];
    mainPane.setHeight(MAIN_H);

    // ── Candlesticks ──────────────────────────────────────────────────────────
    const candleData = prices.map(p => ({
      time:  p.date as unknown as import("lightweight-charts").Time,
      open:  p.open,
      high:  p.high,
      low:   p.low,
      close: p.close,
    }));

    const candles = mainPane.addSeries(CandlestickSeries, {
      upColor:   THEME.up,
      downColor: THEME.down,
      borderUpColor:   THEME.up,
      borderDownColor: THEME.down,
      wickUpColor:   THEME.up,
      wickDownColor: THEME.down,
    });
    candles.setData(candleData);

    // ── Volume ────────────────────────────────────────────────────────────────
    if (indicators.volume) {
      const volData = prices.map(p => ({
        time:  p.date as unknown as import("lightweight-charts").Time,
        value: p.volume,
        color: p.close >= p.open ? "rgba(34,197,94,0.35)" : "rgba(239,68,68,0.35)",
      }));
      const volSeries = mainPane.addSeries(HistogramSeries, {
        priceScaleId: "vol",
        priceFormat: { type: "volume" },
      });
      volSeries.setData(volData);
      chart.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
    }

    const closes = prices.map(p => p.close);

    // ── Moving averages ───────────────────────────────────────────────────────
    const addMaSeries = (period: number, color: string) => {
      const vals = sma(closes, period);
      const maData = prices
        .map((p, i) => vals[i] !== null
          ? { time: p.date as unknown as import("lightweight-charts").Time, value: vals[i]! }
          : null)
        .filter(Boolean) as { time: import("lightweight-charts").Time; value: number }[];
      if (!maData.length) return;
      const s = mainPane.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false });
      s.setData(maData);
    };

    if (indicators.ma20)  addMaSeries(20,  THEME.ma20);
    if (indicators.ma50)  addMaSeries(50,  THEME.ma50);
    if (indicators.ma200) addMaSeries(200, THEME.ma200);

    // ── Bollinger Bands ───────────────────────────────────────────────────────
    if (indicators.bb) {
      const bands = bollingerBands(closes, 20, 2);

      const mkBandData = (key: "upper" | "lower") =>
        prices
          .map((p, i) => bands[i][key] !== null
            ? { time: p.date as unknown as import("lightweight-charts").Time, value: bands[i][key]! }
            : null)
          .filter(Boolean) as { time: import("lightweight-charts").Time; value: number }[];

      const upperData = mkBandData("upper");
      const lowerData = mkBandData("lower");

      const bbUpper = mainPane.addSeries(LineSeries, {
        color: THEME.bbUpper,
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
      });
      bbUpper.setData(upperData);

      const bbLower = mainPane.addSeries(LineSeries, {
        color: THEME.bbLower,
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
      });
      bbLower.setData(lowerData);
    }

    // ── RSI pane ─────────────────────────────────────────────────────────────
    if (showRsi) {
      const rsiPane = chart.addPane();
      rsiPane.setHeight(SUB_H);

      const rsiVals = rsi(closes, 14);
      const rsiData = prices
        .map((p, i) => rsiVals[i] !== null
          ? { time: p.date as unknown as import("lightweight-charts").Time, value: rsiVals[i]! }
          : null)
        .filter(Boolean) as { time: import("lightweight-charts").Time; value: number }[];

      const rsiSeries = rsiPane.addSeries(LineSeries, {
        color: THEME.rsiLine,
        lineWidth: 1,
        priceLineVisible: false,
      });
      rsiSeries.setData(rsiData);

      // Reference lines at 30 / 70
      for (const level of [30, 70]) {
        rsiSeries.createPriceLine({
          price: level,
          color: THEME.border,
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: level === 70 ? "OB" : "OS",
        });
      }
    }

    // ── MACD pane ────────────────────────────────────────────────────────────
    if (showMacd) {
      const macdPane = chart.addPane();
      macdPane.setHeight(SUB_H);

      const { macd: macdLine, signal: signalLine, hist } = macd(closes);

      const mkMacdData = (vals: (number | null)[]) =>
        prices
          .map((p, i) => vals[i] !== null
            ? { time: p.date as unknown as import("lightweight-charts").Time, value: vals[i]! }
            : null)
          .filter(Boolean) as { time: import("lightweight-charts").Time; value: number }[];

      const histData = prices
        .map((p, i) => hist[i] !== null
          ? {
              time:  p.date as unknown as import("lightweight-charts").Time,
              value: hist[i]!,
              color: hist[i]! >= 0 ? THEME.histUp : THEME.histDn,
            }
          : null)
        .filter(Boolean) as { time: import("lightweight-charts").Time; value: number; color: string }[];

      const histSeries = macdPane.addSeries(HistogramSeries, { priceLineVisible: false });
      histSeries.setData(histData);

      const macdSeries = macdPane.addSeries(LineSeries, {
        color: THEME.macdLine,
        lineWidth: 1,
        priceLineVisible: false,
      });
      macdSeries.setData(mkMacdData(macdLine));

      const signalSeries = macdPane.addSeries(LineSeries, {
        color: THEME.signal,
        lineWidth: 1,
        priceLineVisible: false,
      });
      signalSeries.setData(mkMacdData(signalLine));
    }

    chart.timeScale().fitContent();

    // Responsive resize
    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth });
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [prices, indicators]);

  return (
    <div className="bg-slate-900 rounded-lg border border-slate-700 p-3">
      <div className="flex items-center justify-between mb-2 px-1">
        <span className="text-slate-300 font-semibold text-sm">{ticker} — Interactive Chart</span>
        <span className="text-slate-500 text-xs">Scroll to zoom · Drag to pan</span>
      </div>
      <div ref={containerRef} />
    </div>
  );
}
