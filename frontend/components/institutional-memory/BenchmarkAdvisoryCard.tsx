"use client";

import React, { useState, useEffect } from "react";
import { PlanningBenchmark, EvidenceReference } from "@/lib/types";
import { fetchInstitutionalMemoryBenchmarks } from "@/lib/api";
import { ShieldAlert, Compass, CheckCircle2, AlertTriangle, ShieldCheck, ChevronRight } from "lucide-react";
import StatusBadge from "@/components/ui/StatusBadge";

interface BenchmarkAdvisoryCardProps {
  projectId: string;
  onViewEvidence: (title: string, evidence: EvidenceReference[], formula?: string) => void;
}

const BENCHMARK_DISCIPLINES = ["Civil", "Structural", "Piping", "Electrical", "Mechanical"];

interface DisciplineBenchmarkData {
  discipline: string;
  benchmark: PlanningBenchmark | null;
  loading: boolean;
}

export default function BenchmarkAdvisoryCard({
  projectId,
  onViewEvidence,
}: BenchmarkAdvisoryCardProps) {
  const [selectedDisc, setSelectedDisc] = useState<string>("Civil");
  const [benchmarksByDisc, setBenchmarksByDisc] = useState<Record<string, PlanningBenchmark | null>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    setLoading(true);

    // Fetch benchmarks for all standard disciplines in parallel using authoritative backend endpoint
    Promise.all(
      BENCHMARK_DISCIPLINES.map(async (disc) => {
        try {
          const res = await fetchInstitutionalMemoryBenchmarks(projectId, { discipline: disc });
          return { disc, data: res };
        } catch (err) {
          console.error(`Failed to fetch benchmark for ${disc}:`, err);
          return { disc, data: null };
        }
      })
    )
      .then((results) => {
        if (!isMounted) return;
        const map: Record<string, PlanningBenchmark | null> = {};
        for (const item of results) {
          map[item.disc] = item.data;
        }
        setBenchmarksByDisc(map);
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [projectId]);

  const activeBenchmark = benchmarksByDisc[selectedDisc];

  return (
    <div className="bg-white border border-slate-200/90 rounded-xl p-5 shadow-2xs space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
        <div className="flex items-center gap-2">
          <span className="p-1.5 rounded-lg bg-indigo-50 text-indigo-700">
            <Compass className="h-4 w-4" />
          </span>
          <div>
            <h3 className="text-sm font-bold text-slate-900">Historical Benchmark Intelligence</h3>
            <p className="text-[11px] text-slate-500">P50 & P80 pacing percentiles derived from verified execution ledger</p>
          </div>
        </div>

        <span className="text-[11px] font-mono text-slate-400 bg-slate-100 px-2 py-0.5 rounded border border-slate-200">
          Advisory Only
        </span>
      </div>

      {loading ? (
        <div className="py-8 text-center text-xs text-slate-400 animate-pulse">
          Calculating empirical benchmark distribution across disciplines...
        </div>
      ) : (
        <div className="space-y-4">
          {/* Comparison Table */}
          <div className="border border-slate-200/80 rounded-lg overflow-hidden">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50/90 text-slate-600 font-semibold uppercase text-[10px] tracking-wider border-b border-slate-200">
                <tr>
                  <th className="px-3.5 py-2.5">Discipline</th>
                  <th className="px-3 py-2.5 text-center">Sample</th>
                  <th className="px-3 py-2.5 font-mono text-right">P50 (Median)</th>
                  <th className="px-3 py-2.5 font-mono text-right">P80</th>
                  <th className="px-3 py-2.5 text-right">Observed Rate</th>
                  <th className="px-3.5 py-2.5 text-right">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-slate-700">
                {BENCHMARK_DISCIPLINES.map((disc) => {
                  const b = benchmarksByDisc[disc];
                  const isSelected = selectedDisc === disc;
                  const sampleSize = b?.sample_size ?? 0;
                  const hasSufficient = b?.status === "SUFFICIENT_SAMPLE";

                  return (
                    <tr
                      key={disc}
                      onClick={() => setSelectedDisc(disc)}
                      className={`cursor-pointer transition-colors ${
                        isSelected
                          ? "bg-indigo-50/60 font-medium"
                          : "hover:bg-slate-50/70"
                      }`}
                    >
                      <td className="px-3.5 py-2.5 font-semibold text-slate-900 flex items-center gap-1.5">
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${
                            isSelected ? "bg-indigo-600" : "bg-slate-300"
                          }`}
                        />
                        {disc}
                      </td>
                      <td className="px-3 py-2.5 text-center font-mono text-slate-600">
                        {sampleSize}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-right text-slate-900 font-semibold">
                        {b?.median_actual_duration !== null && b?.median_actual_duration !== undefined
                          ? `${b.median_actual_duration}d`
                          : "—"}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-right text-slate-700">
                        {b?.p80_duration !== null && b?.p80_duration !== undefined
                          ? `${b.p80_duration}d`
                          : sampleSize >= 5
                          ? "—"
                          : <span className="text-slate-400 text-[10px]">N/A (&lt;5)</span>}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-600 text-[11px]">
                        {b?.observed_rate !== null && b?.observed_rate !== undefined
                          ? `${b.observed_rate} ${b.rate_unit || ""}`
                          : "—"}
                      </td>
                      <td className="px-3.5 py-2.5 text-right">
                        {sampleSize === 0 ? (
                          <span className="text-[10px] text-slate-400 font-medium">No Data</span>
                        ) : hasSufficient ? (
                          <span className="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                            Authoritative
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[10px] font-bold text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
                            Sparse
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Active Discipline Advisory Detail */}
          {activeBenchmark && (
            <div className="p-3.5 rounded-lg border border-slate-200 bg-slate-50/60 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-900">
                  {selectedDisc} Benchmark Advisory
                </span>
                <span className="text-[11px] text-slate-500 font-mono">
                  Sample: {activeBenchmark.sample_size} completed activities
                </span>
              </div>

              <p className="text-xs text-slate-700 leading-relaxed">
                {activeBenchmark.advisory_message}
              </p>

              {activeBenchmark.evidence.length > 0 && (
                <div className="pt-1.5 flex justify-end">
                  <button
                    onClick={() =>
                      onViewEvidence(
                        `Historical Planning Benchmark: ${selectedDisc}`,
                        activeBenchmark.evidence
                      )
                    }
                    className="inline-flex items-center gap-1.5 text-xs font-bold text-indigo-700 hover:text-indigo-900 transition-colors"
                  >
                    <ShieldCheck className="h-4 w-4" />
                    Inspect Evidence Records ({activeBenchmark.evidence.length})
                    <ChevronRight className="h-3 w-3" />
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Safety Notice */}
      <div className="p-2.5 bg-slate-50 border border-slate-200/80 rounded-lg flex items-center gap-2 text-[11px] text-slate-500">
        <ShieldAlert className="h-4 w-4 text-slate-400 shrink-0" />
        <span>
          Governance Rule: Planning benchmarks are advisory only. ScheduleManager will never automatically mutate planned durations or CPM logic.
        </span>
      </div>
    </div>
  );
}
