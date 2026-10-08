"use client";

import { useEffect, useState } from "react";

import { CalibrationChart } from "@/components/Charts";
import { ErrorNote, Skeleton } from "@/components/ui";
import { api, errorMessage, type ActiveModel, type Calibration, type HorizonMetrics, type PerformanceSummary } from "@/lib/api";
import { date, pct } from "@/lib/format";

const HORIZONS = [7, 30, 90];

export default function ScorecardPage() {
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [rows, setRows] = useState<HorizonMetrics[]>([]);
  const [model, setModel] = useState<ActiveModel | null>(null);
  const [horizon, setHorizon] = useState(30);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.performanceSummary(), api.byHorizon(), api.activeModel()])
      .then(([s, h, m]) => {
        setSummary(s);
        setRows(h.horizons);
        setModel(m);
      })
      .catch((err) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    api.calibration(horizon).then(setCalibration).catch(() => null);
  }, [horizon]);

  if (error) return <main className="page wrap"><ErrorNote>{error}</ErrorNote></main>;
  if (!summary) return <main className="page wrap" aria-busy="true"><Skeleton height="18rem" /></main>;

  const scored = summary.forecasts_evaluated;
  const policy = summary.recommendation_policy.overall;

  return (
    <main className="page wrap">
      <div className="page-head">
        <div>
          <h1 className="display h-lg">The scorecard</h1>
          <p className="body">Every forecast is kept as it was made and marked against what happened once its window closes.</p>
        </div>
        <p className="data muted">
          {summary.forecasts_total} forecasts made · {scored} scored so far
        </p>
      </div>

      <dl className="kv">
        <div>
          <dt>Forecasts come from</dt>
          <dd className="num" style={{ fontSize: "1.125rem" }}>{model?.active ? `Trained model ${model.active.version}` : "The rule baseline"}</dd>
        </div>
        <div>
          <dt>{model?.active ? "In use since" : "Why"}</dt>
          <dd className="num" style={{ fontSize: "1.125rem" }}>
            {model?.active ? date(model.active.activated_at) : "No trained model has beaten it yet"}
          </dd>
        </div>
        <div>
          <dt>Call rules</dt>
          <dd className="num" style={{ fontSize: "1.125rem" }}>{model?.ruleset_version ?? "—"}</dd>
        </div>
      </dl>

      {scored === 0 ? (
        <div className="empty">
          <p className="h-sm">Nothing to score yet</p>
          <p className="body">
            A forecast can only be marked once its shortest window, seven days, has passed. Check back after your first
            forecasts are a week old. Nothing here is filled in with made-up numbers in the meantime.
          </p>
        </div>
      ) : (
        <>
          <section className="split flip" aria-labelledby="cal-title">
            <div className="stack">
              <h2 id="cal-title" className="display h-md">Did the odds hold up?</h2>
              <p className="body">
                When we said a sale had a 70% chance, did it happen about seven times in ten? Squares on the blue diagonal
                mean yes. Bigger squares hold more forecasts.
              </p>
              <div className="seg" role="group" aria-label="Forecast window">
                {HORIZONS.map((h) => (
                  <button key={h} type="button" aria-pressed={horizon === h} onClick={() => setHorizon(h)}>{h} days</button>
                ))}
              </div>
              <p className="data muted">{calibration?.n ?? 0} scored forecasts for this window</p>
            </div>
            <div style={{ maxWidth: "32rem" }}>
              {calibration && calibration.bins.length > 0 ? (
                <CalibrationChart bins={calibration.bins} />
              ) : (
                <div className="empty"><p className="body">No scored forecasts for the {horizon}-day window yet.</p></div>
              )}
            </div>
          </section>

          <section className="module" aria-labelledby="horizon-title">
            <div className="module-head">
              <h2 id="horizon-title" className="h-sm">By forecast window</h2>
              <span className="data muted">Lower error is better</span>
            </div>
            <div className="module-body scroll-x">
              <table className="table">
                <thead>
                  <tr>
                    <th>Window</th>
                    <th>Scored</th>
                    <th>Sales happened</th>
                    <th title="Brier score: average squared gap between the forecast chance and what happened. 0 is perfect, 0.25 is a coin flip.">Miss score</th>
                    <th title="Expected calibration error: how far stated chances were from observed rates, on average.">Odds off by</th>
                    <th title="ROC-AUC: how well higher forecasts separate sales from non-sales. 0.5 is chance, 1 is perfect.">Ranking</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.horizon_days}>
                      <td>{r.horizon_days} days</td>
                      <td>{r.n}</td>
                      <td>{r.positive_rate != null ? pct(r.positive_rate) : "—"}</td>
                      <td>{r.brier != null ? r.brier.toFixed(3) : "—"}</td>
                      <td>{r.ece != null ? pct(r.ece, 1) : "—"}</td>
                      <td>{r.roc_auc != null ? r.roc_auc.toFixed(2) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="data muted" style={{ marginTop: "0.75rem" }}>
                Miss score 0 is perfect and 0.25 is a coin flip · ranking 0.5 is chance and 1 is perfect
              </p>
            </div>
          </section>

          {policy && policy.n > 0 ? (
            <section aria-labelledby="calls-title">
              <h2 id="calls-title" className="display h-md" style={{ marginBottom: "0.75rem" }}>How the calls turned out</h2>
              <dl className="kv">
                <div>
                  <dt>Calls scored (30 days)</dt>
                  <dd className="num">{policy.n}</dd>
                </div>
                <div>
                  <dt>Wait, then it got cheaper</dt>
                  <dd className="num">{policy.wait_followed_by_lower_price_rate != null ? pct(policy.wait_followed_by_lower_price_rate) : "—"}</dd>
                </div>
                <div>
                  <dt>Buy, then it dropped 10%+</dt>
                  <dd className="num">{policy.buy_regret_rate != null ? pct(policy.buy_regret_rate) : "—"}</dd>
                </div>
                <div>
                  <dt>Buy / wait / neutral</dt>
                  <dd className="num">{policy.counts.BUY ?? 0} / {policy.counts.WAIT ?? 0} / {policy.counts.NEUTRAL ?? 0}</dd>
                </div>
              </dl>
            </section>
          ) : null}
        </>
      )}

      <p className="attribution">{summary.disclaimer}</p>
    </main>
  );
}
