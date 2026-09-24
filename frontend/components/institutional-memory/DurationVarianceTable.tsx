"use client";

import React, { useState } from "react";
import { DurationSummary } from "@/lib/types";
import { CheckCircle2, AlertTriangle, Clock, TrendingUp } from "lucide-react";

interface DurationVarianceTableProps {
  durations: DurationSummary | null;
  loading: boolean;
}

export default function DurationVarianceTable({
  durations,
  loading,
}: DurationVarianceTableProps) {
  const [filterStatus, setFilterStatus] = useState<"ALL" | "ON_TIME" | "DELAYED">("ALL");

  if (loading) {
    return (
      <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-slate-400 animate-pulse text-xs">
        Loading planned vs actual durations from PostgreSQL...
      </div>
    );
  }

  if (!durations || durations.activities_completed === 0) {
    return (
      <div className="bg-white border border-slate-200 rounded-xl p-12 text-center text-slate-400 text-xs">
        No completed activities with verified start and finish dates found in project history.
      </div>
    );
  }

  const filteredItems = durations.items.filter((item) => {
    if (filterStatus === "ON_TIME") return item.is_on_time;
    if (filterStatus === "DELAYED") return !item.is_on_time;
    return true;
  });

  const maxDuration = Math.max(
    ...durations.items.map((i) => Math.max(i.planned_duration_days || 0, i.actual_duration_days || 0)),
    30
  );

  return (
    <div className="space-y-4">
      {/* Top Distribution Metric Strip */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="bg-white border border-slate-200/90 rounded-lg p-3.5 shadow-2xs">
          <span className="text-[11px] text-slate-500 font-bold uppercase tracking-wider block">
            Avg Planned Duration
          </span>
          <span className="text-xl font-bold font-mono text-slate-900 mt-1 block">
            {durations.average_planned_duration}d
          </span>
        </div>

        <div className="bg-white border border-slate-200/90 rounded-lg p-3.5 shadow-2xs">
          <span className="text-[11px] text-slate-500 font-bold uppercase tracking-wider block">
            Avg Actual Duration
          </span>
          <span className="text-xl font-bold font-mono text-slate-900 mt-1 block">
            {durations.average_actual_duration}d
          </span>
        </div>

        <div className="bg-white border border-slate-200/90 rounded-lg p-3.5 shadow-2xs">
          <span className="text-[11px] text-slate-500 font-bold uppercase tracking-wider block">
            Average Variance
          </span>
          <span
            className={`text-xl font-bold font-mono mt-1 block ${
              durations.average_variance_days > 0 ? "text-amber-600" : "text-emerald-600"
            }`}
          >
            {durations.average_variance_days > 0
              ? `+${durations.average_variance_days}`
              : durations.average_variance_days}d
          </span>
        </div>

        <div className="bg-white border border-slate-200/90 rounded-lg p-3.5 shadow-2xs">
          <span className="text-[11px] text-slate-500 font-bold uppercase tracking-wider block">
            Pacing Distribution
          </span>
          <div className="text-xs font-mono text-slate-700 mt-1 space-y-0.5">
            <div>
              P50 (Median):{" "}
              <strong className="text-slate-900">
                {durations.p50_duration !== null && durations.p50_duration !== undefined
                  ? `${durations.p50_duration}d`
                  : "N/A (<5)"}
              </strong>
            </div>
            <div>
              P80 (Pacing):{" "}
              <strong className="text-slate-900">
                {durations.p80_duration !== null && durations.p80_duration !== undefined
                  ? `${durations.p80_duration}d`
                  : "N/A (<5)"}
              </strong>
            </div>
          </div>
        </div>
      </div>

      {/* Table Card */}
      <div className="bg-white border border-slate-200/90 rounded-xl shadow-2xs overflow-hidden">
        <div className="px-5 py-3.5 border-b border-slate-200 flex flex-wrap items-center justify-between gap-4 bg-slate-50/60">
          <div>
            <h3 className="text-sm font-bold text-slate-900">Planned vs Actual Duration Comparison</h3>
            <p className="text-[11px] text-slate-500 mt-0.5">
              Empirical execution variance calculated from completed activity start/finish dates
            </p>
          </div>

          {/* Filter Pills */}
          <div className="flex items-center gap-1 bg-slate-100 p-1 rounded-lg border border-slate-200 text-xs">
            <button
              onClick={() => setFilterStatus("ALL")}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                filterStatus === "ALL"
                  ? "bg-white text-slate-900 shadow-2xs font-bold"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              All ({durations.items.length})
            </button>
            <button
              onClick={() => setFilterStatus("ON_TIME")}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                filterStatus === "ON_TIME"
                  ? "bg-white text-emerald-800 shadow-2xs font-bold"
                  : "text-slate-600 hover:text-emerald-800"
              }`}
            >
              On Time ({durations.on_time_count})
            </button>
            <button
              onClick={() => setFilterStatus("DELAYED")}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                filterStatus === "DELAYED"
                  ? "bg-white text-amber-800 shadow-2xs font-bold"
                  : "text-slate-600 hover:text-amber-800"
              }`}
            >
              Delayed ({durations.delayed_count})
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-600 font-semibold uppercase text-[10px] tracking-wider border-b border-slate-200">
              <tr>
                <th className="px-5 py-3">Activity</th>
                <th className="px-4 py-3">Discipline</th>
                <th className="px-4 py-3">Planned vs Actual</th>
                <th className="px-4 py-3">Variance</th>
                <th className="px-4 py-3">Execution Dates</th>
                <th className="px-5 py-3 text-right">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {filteredItems.map((item) => {
                const plannedPct = Math.min(100, Math.round((item.planned_duration_days / maxDuration) * 100));
                const actualPct = Math.min(100, Math.round((item.actual_duration_days / maxDuration) * 100));

                return (
                  <tr key={item.activity_id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="px-5 py-3.5">
                      <div className="font-mono font-bold text-slate-900">{item.activity_code}</div>
                      <div className="text-[11px] text-slate-500 max-w-[200px] truncate" title={item.activity_name}>
                        {item.activity_name}
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-slate-600 font-medium">
                      {item.discipline || <span className="text-slate-400 italic">—</span>}
                    </td>
                    <td className="px-4 py-3.5 min-w-[200px]">
                      {/* Visual Duration Comparison Bars */}
                      <div className="space-y-1">
                        <div className="flex items-center justify-between text-[10px] font-mono text-slate-500">
                          <span>Plan: <strong>{item.planned_duration_days}d</strong></span>
                          <span>Act: <strong>{item.actual_duration_days}d</strong></span>
                        </div>
                        <div className="w-full bg-slate-100 rounded h-1.5 overflow-hidden flex flex-col justify-center">
                          <div
                            className="bg-slate-400 h-1 rounded"
                            style={{ width: `${plannedPct}%` }}
                            title={`Planned: ${item.planned_duration_days}d`}
                          />
                        </div>
                        <div className="w-full bg-slate-100 rounded h-1.5 overflow-hidden flex flex-col justify-center">
                          <div
                            className={`h-1 rounded ${
                              item.is_on_time ? "bg-emerald-500" : "bg-amber-500"
                            }`}
                            style={{ width: `${actualPct}%` }}
                            title={`Actual: ${item.actual_duration_days}d`}
                          />
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 font-mono">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${
                          item.variance_days > 0
                            ? "bg-amber-50 text-amber-800 border border-amber-200"
                            : item.variance_days < 0
                            ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
                            : "bg-slate-100 text-slate-700"
                        }`}
                      >
                        {item.variance_days > 0 ? `+${item.variance_days}` : item.variance_days}d
                        <span className="text-[10px] ml-1 opacity-80">
                          ({item.variance_percent > 0 ? `+${item.variance_percent}` : item.variance_percent}%)
                        </span>
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-[11px] font-mono text-slate-500">
                      {item.actual_start} → {item.actual_finish}
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      {item.is_on_time ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
                          <CheckCircle2 className="h-3 w-3" />
                          On Time
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200">
                          <AlertTriangle className="h-3 w-3" />
                          Delayed
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
