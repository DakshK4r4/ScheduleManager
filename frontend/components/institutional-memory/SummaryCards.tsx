"use client";

import React from "react";
import { CheckCircle2, Database, Clock, Layers, ShieldCheck } from "lucide-react";
import { InstitutionalMemorySummary } from "@/lib/types";
import MetricCard from "@/components/ui/MetricCard";

interface SummaryCardsProps {
  summary: InstitutionalMemorySummary | null;
  loading: boolean;
}

export default function SummaryCards({ summary, loading }: SummaryCardsProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 animate-pulse">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-28 bg-slate-100 rounded-xl border border-slate-200" />
        ))}
      </div>
    );
  }

  if (!summary) return null;

  const totalQuantitiesRecorded = Object.values(summary.total_quantity_by_unit).reduce(
    (acc, val) => acc + val,
    0
  );

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Card 1: Verified Records */}
        <MetricCard
          label="Verified Events"
          value={summary.verified_event_count}
          subtext={`${summary.ledger_entry_count} progress ledger entries`}
          icon={CheckCircle2}
          tone="intelligence"
        />

        {/* Card 2: Completed Activities */}
        <MetricCard
          label="Completed Activities"
          value={summary.completed_activity_count}
          subtext={`${summary.data_quality.completed_activities_with_dates || 0} with start/finish dates`}
          icon={Database}
          tone="success"
        />

        {/* Card 3: Observed Quantities */}
        <MetricCard
          label="Installed Quantities"
          value={totalQuantitiesRecorded > 0 ? totalQuantitiesRecorded.toLocaleString() : "0"}
          subtext={
            Object.keys(summary.total_quantity_by_unit).length === 0
              ? "No quantities recorded"
              : Object.entries(summary.total_quantity_by_unit)
                  .map(([unit, qty]) => `${qty.toLocaleString()} ${unit}`)
                  .join(" • ")
          }
          icon={Layers}
          tone="warning"
        />

        {/* Card 4: Average Completed Duration */}
        <MetricCard
          label="Average Actual Duration"
          value={
            summary.average_actual_duration !== null && summary.average_actual_duration !== undefined
              ? `${summary.average_actual_duration}d`
              : "—"
          }
          subtext={
            summary.average_duration_variance !== null && summary.average_duration_variance !== undefined
              ? `Avg Variance: ${summary.average_duration_variance > 0 ? `+${summary.average_duration_variance}` : summary.average_duration_variance}d`
              : "Based on completed activities"
          }
          icon={Clock}
          tone={
            summary.average_duration_variance !== null &&
            summary.average_duration_variance !== undefined &&
            summary.average_duration_variance > 0
              ? "warning"
              : "primary"
          }
        />
      </div>

      {/* Data Quality Transparency Strip */}
      <div className="bg-slate-50/80 border border-slate-200/80 rounded-lg px-4 py-2.5 flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-2.5 text-slate-700">
          <ShieldCheck className="h-4 w-4 text-emerald-600" />
          <span className="font-semibold text-slate-900">Institutional Coverage Audit:</span>
          <span className="text-slate-600 font-mono">
            {summary.data_quality.usable_ledger_entries || 0} Ledger Rows
          </span>
          <span className="text-slate-300">•</span>
          <span className="text-slate-600 font-mono">
            {summary.data_quality.records_with_quantity || 0} with Quantities
          </span>
          <span className="text-slate-300">•</span>
          <span className="text-slate-600 font-mono">
            {summary.data_quality.completed_activities_with_dates || 0} Completed with Dates
          </span>
        </div>
        <div className="text-slate-500 font-mono text-[11px]">
          Latest Progress Date: <strong className="text-slate-700">{summary.latest_reporting_date || "N/A"}</strong>
        </div>
      </div>
    </div>
  );
}
