"use client";

import React, { useState, useEffect, useMemo } from "react";
import {
  Calendar,
  ZoomIn,
  ZoomOut,
  RefreshCw,
  ShieldAlert,
  Clock,
  Layers,
  Check,
} from "lucide-react";
import { fetchActivities, fetchProjectCPM } from "@/lib/api";
import { Activity, CPMResult, CPMActivityNode } from "@/lib/types";

interface GanttChartProps {
  projectId: string;
  projectDataDate?: string | null;
  onEditActivity?: (activity: Activity) => void;
}

export default function GanttChart({
  projectId,
  projectDataDate,
  onEditActivity,
}: GanttChartProps) {
  const [activities, setActivities] = useState<Activity[]>([]);
  const [cpmMap, setCpmMap] = useState<Record<string, CPMActivityNode>>({});
  const [criticalCodes, setCriticalCodes] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [zoomLevel, setZoomLevel] = useState<"days" | "weeks">("weeks");

  // Overlays Toolbar State
  const [showCritical, setShowCritical] = useState(true);
  const [showBaseline, setShowBaseline] = useState(true);
  const [showFloat, setShowFloat] = useState(true);
  const [showToday, setShowToday] = useState(true);

  const loadData = () => {
    setLoading(true);
    Promise.all([
      fetchActivities(projectId, {
        page_size: 150,
        sort_by: "planned_start",
        sort_dir: "asc",
      }),
      fetchProjectCPM(projectId).catch(() => null),
    ])
      .then(([actRes, cpm]) => {
        const withDates = actRes.items.filter((a) => a.planned_start && a.planned_finish);
        setActivities(withDates.length > 0 ? withDates : actRes.items);
        if (cpm) {
          setCpmMap(cpm.activities || {});
          setCriticalCodes(new Set(cpm.critical_activities || []));
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, [projectId]);

  // Compute timeline boundaries
  const { minDate, maxDate, totalDays } = useMemo(() => {
    if (activities.length === 0) {
      const now = new Date();
      const end = new Date(now);
      end.setDate(end.getDate() + 60);
      return { minDate: now, maxDate: end, totalDays: 60 };
    }

    let earliest: Date | null = null;
    let latest: Date | null = null;

    for (const a of activities) {
      if (a.planned_start) {
        const s = new Date(a.planned_start);
        if (!isNaN(s.getTime())) {
          if (!earliest || s < earliest) earliest = s;
        }
      }
      if (a.planned_finish) {
        const f = new Date(a.planned_finish);
        if (!isNaN(f.getTime())) {
          if (!latest || f > latest) latest = f;
        }
      }
    }

    if (!earliest || !latest || isNaN(earliest.getTime()) || isNaN(latest.getTime())) {
      const now = new Date();
      const end = new Date(now);
      end.setDate(end.getDate() + 60);
      return { minDate: now, maxDate: end, totalDays: 60 };
    }

    // Add padding days
    earliest = new Date(earliest);
    earliest.setDate(earliest.getDate() - 3);
    latest = new Date(latest);
    latest.setDate(latest.getDate() + 7);

    const diff = Math.max(7, Math.ceil((latest.getTime() - earliest.getTime()) / (1000 * 3600 * 24)));
    return { minDate: earliest, maxDate: latest, totalDays: diff };
  }, [activities]);

  const dayWidth = zoomLevel === "days" ? 32 : 12;
  const timelineWidth = totalDays * dayWidth;

  const getPosition = (startStr: string | null, finishStr: string | null) => {
    if (!startStr) return { left: 0, width: 40 };

    const start = new Date(startStr);
    if (isNaN(start.getTime())) return { left: 0, width: 40 };
    const finish = finishStr ? new Date(finishStr) : new Date(start.getTime() + 24 * 3600 * 1000);
    const validFinish = isNaN(finish.getTime()) ? new Date(start.getTime() + 24 * 3600 * 1000) : finish;

    const startDiff = (start.getTime() - minDate.getTime()) / (1000 * 3600 * 24);
    const durDays = Math.max(1, (validFinish.getTime() - start.getTime()) / (1000 * 3600 * 24));

    const left = Math.max(0, startDiff * dayWidth);
    const width = Math.max(16, durDays * dayWidth);

    return { left, width };
  };

  // Today marker offset in pixels
  const todayOffset = useMemo(() => {
    const todayDate = projectDataDate ? new Date(projectDataDate) : new Date();
    if (isNaN(todayDate.getTime())) return null;
    const diff = (todayDate.getTime() - minDate.getTime()) / (1000 * 3600 * 24);
    if (diff < 0 || diff > totalDays) return null;
    return diff * dayWidth;
  }, [projectDataDate, minDate, totalDays, dayWidth]);

  // Generate date markers for header
  const headerMarks = useMemo(() => {
    const marks = [];
    const step = zoomLevel === "days" ? 1 : 7;
    for (let d = 0; d < totalDays; d += step) {
      const curDate = new Date(minDate);
      curDate.setDate(curDate.getDate() + d);
      marks.push({
        offset: d * dayWidth,
        label: curDate.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      });
    }
    return marks;
  }, [minDate, totalDays, dayWidth, zoomLevel]);

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-2xs space-y-4">
      {/* Header controls & Overlays Toolbar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-slate-100 pb-3.5">
        <div>
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-900">
            Schedule Gantt Timeline
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Interactive Critical Path, Baseline vs Actual comparison, and Float overlays
          </p>
        </div>

        {/* Toolbar: Overlays & Zoom */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Overlays toggle buttons */}
          <div className="flex items-center rounded-md border border-slate-200 bg-slate-50 p-0.5 text-xs">
            <button
              onClick={() => setShowCritical(!showCritical)}
              className={`px-2.5 py-1 rounded font-medium transition-colors flex items-center gap-1 ${
                showCritical
                  ? "bg-rose-50 text-rose-700 font-semibold border border-rose-200"
                  : "text-slate-500 hover:text-slate-800"
              }`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${showCritical ? "bg-rose-600" : "bg-slate-300"}`} />
              <span>Critical</span>
            </button>

            <button
              onClick={() => setShowBaseline(!showBaseline)}
              className={`px-2.5 py-1 rounded font-medium transition-colors ${
                showBaseline
                  ? "bg-white text-blue-700 shadow-2xs font-semibold"
                  : "text-slate-500 hover:text-slate-800"
              }`}
            >
              Baseline
            </button>

            <button
              onClick={() => setShowFloat(!showFloat)}
              className={`px-2.5 py-1 rounded font-medium transition-colors ${
                showFloat
                  ? "bg-white text-blue-700 shadow-2xs font-semibold"
                  : "text-slate-500 hover:text-slate-800"
              }`}
            >
              Float
            </button>

            <button
              onClick={() => setShowToday(!showToday)}
              className={`px-2.5 py-1 rounded font-medium transition-colors ${
                showToday
                  ? "bg-white text-blue-700 shadow-2xs font-semibold"
                  : "text-slate-500 hover:text-slate-800"
              }`}
            >
              Data Date
            </button>
          </div>

          {/* Status Color Legend */}
          <div className="hidden lg:flex items-center gap-3 text-[11px] text-slate-600 font-medium px-2.5 py-1 rounded-md bg-slate-50 border border-slate-200">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-600" />
              <span>Complete</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-blue-600" />
              <span>In Progress</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-slate-400" />
              <span>Not Started</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-rose-600" />
              <span>Critical</span>
            </span>
          </div>

          {/* Zoom: Weeks / Days */}
          <div className="flex items-center rounded-md border border-slate-200 bg-slate-50 p-0.5 text-xs">
            <button
              onClick={() => setZoomLevel("weeks")}
              className={`px-2.5 py-1 rounded font-medium transition-colors ${
                zoomLevel === "weeks"
                  ? "bg-white text-blue-600 shadow-2xs font-semibold"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Weeks
            </button>
            <button
              onClick={() => setZoomLevel("days")}
              className={`px-2.5 py-1 rounded font-medium transition-colors ${
                zoomLevel === "days"
                  ? "bg-white text-blue-600 shadow-2xs font-semibold"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Days
            </button>
          </div>

          <button
            onClick={loadData}
            title="Refresh Timeline"
            className="rounded-md border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 transition-colors"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin text-blue-600" : ""}`} />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="py-16 text-center text-xs text-slate-400">Loading Gantt timeline...</div>
      ) : activities.length === 0 ? (
        <div className="py-16 text-center text-xs text-slate-400">
          No activities available to plot on timeline.
        </div>
      ) : (
        <div className="flex border border-slate-200 rounded-lg overflow-hidden bg-white">
          {/* Left frozen columns: Activity Info */}
          <div className="w-72 sm:w-80 flex-shrink-0 border-r border-slate-200 bg-slate-50/50">
            <div className="h-10 border-b border-slate-200 px-3.5 flex items-center justify-between font-bold text-[11px] uppercase tracking-wider text-slate-500 bg-slate-100/80">
              <span>Activity</span>
              <span>Dur (d)</span>
            </div>
            <div className="divide-y divide-slate-100">
              {activities.map((act) => {
                const node = cpmMap[act.id] || cpmMap[act.activity_code];
                const isCrit = criticalCodes.has(act.activity_code) || (node && node.is_critical);
                const pct = Math.min(100, Math.max(0, act.percent_complete || 0));
                const isComplete = act.status === "COMPLETED" || pct === 100;
                const isInProgress = act.status === "IN_PROGRESS" || (pct > 0 && pct < 100);

                return (
                  <div
                    key={act.id}
                    onClick={() => onEditActivity && onEditActivity(act)}
                    className={`h-12 px-3.5 flex items-center justify-between hover:bg-slate-50 cursor-pointer transition-colors ${
                      isCrit && showCritical ? "border-l-3 border-l-rose-500 bg-rose-50/20" : ""
                    }`}
                  >
                    <div className="min-w-0 pr-2">
                      <div className="font-semibold text-slate-900 text-xs truncate">
                        {act.name}
                      </div>
                      <div className="font-mono text-[10px] flex items-center gap-1.5">
                        <span className="text-slate-500">{act.activity_code}</span>
                        {isCrit && showCritical ? (
                          <span className="text-rose-600 font-bold">• CRITICAL</span>
                        ) : isComplete ? (
                          <span className="text-emerald-700 font-semibold">• 100%</span>
                        ) : isInProgress ? (
                          <span className="text-blue-700 font-semibold">• {pct}%</span>
                        ) : null}
                      </div>
                    </div>
                    <span className="text-[11px] font-mono text-slate-500 shrink-0">
                      {act.original_duration !== null ? `${act.original_duration}d` : ""}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Right scrollable timeline area */}
          <div className="flex-1 overflow-x-auto">
            <div style={{ width: `${timelineWidth}px` }} className="relative">
              {/* Timeline date header */}
              <div className="h-10 border-b border-slate-200 bg-slate-100/80 relative">
                {headerMarks.map((m, idx) => (
                  <div
                    key={idx}
                    style={{ left: `${m.offset}px` }}
                    className="absolute top-0 bottom-0 border-l border-slate-200/80 pl-1.5 flex items-center text-[10px] font-mono font-medium text-slate-500 whitespace-nowrap select-none"
                  >
                    {m.label}
                  </div>
                ))}

                {/* Today header marker */}
                {showToday && todayOffset !== null && (
                  <div
                    style={{ left: `${todayOffset}px` }}
                    className="absolute top-1 -translate-x-1/2 z-30"
                  >
                    <span className="rounded bg-rose-600 text-white px-1.5 py-0.2 text-[9px] font-bold font-mono shadow-xs uppercase">
                      Data Date
                    </span>
                  </div>
                )}
              </div>

              {/* Rows */}
              <div className="divide-y divide-slate-100 relative">
                {/* Vertical grid lines */}
                {headerMarks.map((m, idx) => (
                  <div
                    key={idx}
                    style={{ left: `${m.offset}px` }}
                    className="absolute top-0 bottom-0 border-l border-slate-100 pointer-events-none"
                  />
                ))}

                {/* Vertical Today / Data Date indicator line */}
                {showToday && todayOffset !== null && (
                  <div
                    style={{ left: `${todayOffset}px` }}
                    className="absolute top-0 bottom-0 w-px border-l-2 border-dashed border-rose-500 pointer-events-none z-20"
                  />
                )}

                {activities.map((act) => {
                  const node = cpmMap[act.id] || cpmMap[act.activity_code];
                  const isCrit = criticalCodes.has(act.activity_code) || (node && node.is_critical);
                  const pos = getPosition(act.planned_start, act.planned_finish);
                  const pct = Math.min(100, Math.max(0, act.percent_complete || 0));
                  const floatDays = node && node.total_float !== undefined ? node.total_float : null;

                  const isComplete = act.status === "COMPLETED" || pct === 100;
                  const isInProgress = act.status === "IN_PROGRESS" || (pct > 0 && pct < 100);

                  let barBgClass = "bg-slate-400 border-slate-500";
                  if (isCrit && showCritical) {
                    barBgClass = "bg-rose-600 border-rose-700";
                  } else if (isComplete) {
                    barBgClass = "bg-emerald-600 border-emerald-700";
                  } else if (isInProgress) {
                    barBgClass = "bg-blue-600 border-blue-700";
                  }

                  return (
                    <div
                      key={act.id}
                      onClick={() => onEditActivity && onEditActivity(act)}
                      className="h-12 relative flex items-center hover:bg-slate-50/50 cursor-pointer"
                    >
                      {/* Bar Container */}
                      <div
                        style={{
                          left: `${pos.left}px`,
                          width: `${pos.width}px`,
                        }}
                        className="absolute h-6 flex flex-col justify-center select-none group"
                      >
                        {/* Baseline thin track if enabled */}
                        {showBaseline && (
                          <div className="absolute -top-1.5 left-0 right-0 h-1 bg-slate-300 rounded-xs opacity-75" title="Planned Baseline" />
                        )}

                        {/* Main Activity Progress Bar with multi-color status */}
                        <div
                          title={`${act.activity_code}: ${act.name}\nStatus: ${act.status}\nStart: ${act.planned_start || "N/A"}\nFinish: ${act.planned_finish || "N/A"}\nDuration: ${act.original_duration || 0}d\nProgress: ${pct}%${floatDays !== null ? `\nTotal Float: ${floatDays}d` : ""}${isCrit ? "\nCritical: YES" : ""}`}
                          className={`h-6 rounded-md relative overflow-hidden shadow-2xs border transition-transform hover:scale-[1.01] flex items-center ${barBgClass}`}
                        >
                          {/* Shaded Progress Fill */}
                          {pct > 0 && pct < 100 && (
                            <div
                              style={{ width: `${pct}%` }}
                              className="h-full bg-white/25 border-r border-white/40 transition-all"
                            />
                          )}

                          {/* Activity Code and Progress Label in crisp white text */}
                          <span className="absolute inset-0 flex items-center px-2 text-[10px] font-bold text-white truncate drop-shadow-xs pointer-events-none font-mono">
                            {act.activity_code} {pct > 0 ? `(${pct}%)` : ""}
                          </span>
                        </div>

                        {/* Float indicator label next to bar */}
                        {showFloat && floatDays !== null && (
                          <span
                            style={{ left: `${pos.width + 8}px` }}
                            className={`absolute top-1 text-[10px] font-mono whitespace-nowrap font-bold ${
                              floatDays < 0
                                ? "text-rose-600"
                                : floatDays === 0
                                ? "text-rose-700"
                                : "text-slate-500"
                            }`}
                          >
                            TF: {floatDays > 0 ? `+${floatDays}d` : `${floatDays}d`}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
