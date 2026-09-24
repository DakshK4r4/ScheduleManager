"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  Search,
  SlidersHorizontal,
  ChevronLeft,
  ChevronRight,
  ArrowUpDown,
  Edit2,
  Trash2,
  Plus,
  RefreshCw,
  AlertCircle,
  ShieldAlert,
} from "lucide-react";
import { fetchActivities, deleteActivity, fetchProjectWbs, fetchProjectCPM } from "@/lib/api";
import { Activity, ActivityStatus, WBSNode, CPMResult, CPMActivityNode } from "@/lib/types";
import StatusBadge from "./ui/StatusBadge";

interface ActivityTableProps {
  projectId: string;
  onEditActivity: (activity: Activity) => void;
  onAddActivity: () => void;
  refreshTrigger: number;
}

export default function ActivityTable({
  projectId,
  onEditActivity,
  onAddActivity,
  refreshTrigger,
}: ActivityTableProps) {
  const [activities, setActivities] = useState<Activity[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(25);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);

  // CPM Telemetry
  const [cpmMap, setCpmMap] = useState<Record<string, CPMActivityNode>>({});
  const [criticalCodes, setCriticalCodes] = useState<Set<string>>(new Set());
  const [negativeFloatCodes, setNegativeFloatCodes] = useState<Set<string>>(new Set());

  // Filters
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [wbsFilter, setWbsFilter] = useState<string>("");
  const [priorityFilter, setPriorityFilter] = useState<string>("");
  const [sortBy, setSortBy] = useState<string>("activity_code");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // WBS List for filter
  const [wbsList, setWbsList] = useState<WBSNode[]>([]);

  useEffect(() => {
    fetchProjectWbs(projectId)
      .then(setWbsList)
      .catch((err) => console.error("Error loading WBS:", err));

    fetchProjectCPM(projectId)
      .then((cpm) => {
        setCpmMap(cpm.activities || {});
        setCriticalCodes(new Set(cpm.critical_activities || []));
        setNegativeFloatCodes(new Set(cpm.negative_float_activities || []));
      })
      .catch((err) => console.warn("CPM calculation error:", err));
  }, [projectId, refreshTrigger]);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchActivities(projectId, {
        search: searchTerm || undefined,
        status: statusFilter || undefined,
        wbs_id: wbsFilter || undefined,
        sort_by: sortBy,
        sort_dir: sortDir,
        page,
        page_size: pageSize,
      });

      let items = data.items;
      if (priorityFilter === "CRITICAL") {
        items = items.filter(
          (a) =>
            criticalCodes.has(a.activity_code) ||
            (cpmMap[a.id]?.is_critical || cpmMap[a.activity_code]?.is_critical)
        );
      } else if (priorityFilter === "NEGATIVE_FLOAT") {
        items = items.filter(
          (a) =>
            negativeFloatCodes.has(a.activity_code) ||
            (cpmMap[a.id]?.has_negative_float || cpmMap[a.activity_code]?.has_negative_float)
        );
      }

      setActivities(items);
      setTotal(priorityFilter ? items.length : data.total);
      setTotalPages(priorityFilter ? 1 : data.total_pages);
    } catch (err) {
      console.error("Failed to load activities:", err);
    } finally {
      setLoading(false);
    }
  }, [
    projectId,
    searchTerm,
    statusFilter,
    wbsFilter,
    priorityFilter,
    sortBy,
    sortDir,
    page,
    pageSize,
    criticalCodes,
    negativeFloatCodes,
    cpmMap,
  ]);

  useEffect(() => {
    loadData();
  }, [loadData, refreshTrigger]);

  const handleSort = (column: string) => {
    if (sortBy === column) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortBy(column);
      setSortDir("asc");
    }
    setPage(1);
  };

  const handleDelete = async (activity: Activity) => {
    if (
      confirm(
        `Are you sure you want to delete activity '${activity.activity_code} - ${activity.name}'?`
      )
    ) {
      try {
        await deleteActivity(activity.id);
        loadData();
      } catch (err: any) {
        alert(err.message || "Failed to delete activity.");
      }
    }
  };

  const formatDate = (dtStr: string | null) => {
    if (!dtStr) return "-";
    try {
      const d = new Date(dtStr);
      return d.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    } catch {
      return dtStr;
    }
  };

  // Compute schedule finish variance in working days (status-aware & deterministic)
  const computeVariance = (act: Activity, node?: CPMActivityNode): string => {
    // 1. If backend CPM node has calculated finish_variance, use it directly
    if (node && node.finish_variance !== undefined && node.finish_variance !== null) {
      const v = node.finish_variance;
      if (v === 0) return "0d";
      return v > 0 ? `+${v}d` : `${v}d`;
    }
    // 2. Fallback using status-aware dates:
    // Completed: Actual Finish - Planned Finish
    // In-Progress / Not-Started: Forecast Finish - Planned Finish
    const targetFinish =
      act.status === "COMPLETED" && act.actual_finish
        ? act.actual_finish
        : node?.forecast_finish || act.planned_finish;

    if (targetFinish && act.planned_finish) {
      const diffDays = Math.round(
        (new Date(targetFinish).getTime() - new Date(act.planned_finish).getTime()) /
          (1000 * 3600 * 24)
      );
      if (diffDays === 0) return "0d";
      return diffDays > 0 ? `+${diffDays}d` : `${diffDays}d`;
    }
    return "-";
  };

  return (
    <div className="space-y-4">
      {/* Search & Filter Header Bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between bg-white p-3.5 rounded-lg border border-slate-200 shadow-2xs">
        <div className="flex flex-wrap items-center gap-2.5 flex-1">
          {/* Search Box */}
          <div className="relative min-w-[220px] flex-1 max-w-xs">
            <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              placeholder="Search activity code or name..."
              value={searchTerm}
              onChange={(e) => {
                setSearchTerm(e.target.value);
                setPage(1);
              }}
              className="w-full rounded-md border border-slate-300 bg-white pl-8 pr-3 py-1.5 text-xs text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 font-mono"
            />
          </div>

          {/* Status Filter */}
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(1);
            }}
            className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="">All Statuses</option>
            <option value="NOT_STARTED">Not Started</option>
            <option value="IN_PROGRESS">In Progress</option>
            <option value="COMPLETED">Completed</option>
          </select>

          {/* Priority / Criticality Filter */}
          <select
            value={priorityFilter}
            onChange={(e) => {
              setPriorityFilter(e.target.value);
              setPage(1);
            }}
            className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="">All Priorities</option>
            <option value="CRITICAL">Critical Path (Float ≤ 0)</option>
            <option value="NEGATIVE_FLOAT">Negative Float</option>
          </select>

          {/* WBS Filter */}
          {wbsList.length > 0 && (
            <select
              value={wbsFilter}
              onChange={(e) => {
                setWbsFilter(e.target.value);
                setPage(1);
              }}
              className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 max-w-[180px] truncate"
            >
              <option value="">All WBS</option>
              {wbsList.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.code} - {w.name}
                </option>
              ))}
            </select>
          )}

          <button
            onClick={loadData}
            title="Refresh Activities"
            className="rounded-md border border-slate-300 p-1.5 text-slate-600 hover:bg-slate-50 transition-colors"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin text-blue-600" : ""}`} />
          </button>
        </div>

        <button
          onClick={onAddActivity}
          className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-2xs hover:bg-blue-500 transition-colors whitespace-nowrap self-start sm:self-auto"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>Add Activity</span>
        </button>
      </div>

      {/* Spreadsheet Grid Table */}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xs">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs text-slate-600">
            <thead className="bg-slate-50 text-[11px] uppercase font-semibold text-slate-500 border-b border-slate-200">
              <tr>
                <th
                  onClick={() => handleSort("activity_code")}
                  className="px-3.5 py-2.5 cursor-pointer hover:text-slate-900 transition-colors whitespace-nowrap"
                >
                  <div className="flex items-center gap-1">
                    <span>Activity ID</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort("name")}
                  className="px-3.5 py-2.5 cursor-pointer hover:text-slate-900 transition-colors"
                >
                  <div className="flex items-center gap-1">
                    <span>Activity Name</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th className="px-3 py-2.5 whitespace-nowrap">WBS</th>
                <th
                  onClick={() => handleSort("status")}
                  className="px-3 py-2.5 cursor-pointer hover:text-slate-900 transition-colors"
                >
                  <div className="flex items-center gap-1">
                    <span>Status</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort("planned_start")}
                  className="px-3 py-2.5 cursor-pointer hover:text-slate-900 transition-colors whitespace-nowrap"
                >
                  <div className="flex items-center gap-1">
                    <span>Planned Start</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort("planned_finish")}
                  className="px-3 py-2.5 cursor-pointer hover:text-slate-900 transition-colors whitespace-nowrap"
                >
                  <div className="flex items-center gap-1">
                    <span>Planned Finish</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th className="px-3 py-2.5 whitespace-nowrap">Forecast Finish</th>
                <th
                  className="px-3 py-2.5 whitespace-nowrap text-right font-mono"
                  title="Finish variance between the planned/baseline finish and the current forecast or actual finish."
                >
                  <span className="cursor-help underline decoration-dotted decoration-slate-400">
                    Finish Var
                  </span>
                </th>
                <th className="px-3 py-2.5 whitespace-nowrap text-right font-mono">Float</th>
                <th className="px-2.5 py-2.5 whitespace-nowrap text-center">Critical</th>
                <th
                  onClick={() => handleSort("original_duration")}
                  className="px-3 py-2.5 cursor-pointer hover:text-slate-900 transition-colors whitespace-nowrap text-right"
                >
                  <div className="flex items-center justify-end gap-1">
                    <span>Dur (d)</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th
                  onClick={() => handleSort("percent_complete")}
                  className="px-3 py-2.5 cursor-pointer hover:text-slate-900 transition-colors whitespace-nowrap"
                >
                  <div className="flex items-center gap-1">
                    <span>% Complete</span>
                    <ArrowUpDown className="h-3 w-3 text-slate-400" />
                  </div>
                </th>
                <th className="px-3 py-2.5 text-right whitespace-nowrap">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-normal">
              {loading ? (
                <tr>
                  <td colSpan={13} className="py-12 text-center text-slate-400">
                    Loading schedule activities &amp; CPM metrics...
                  </td>
                </tr>
              ) : activities.length === 0 ? (
                <tr>
                  <td colSpan={13} className="py-12 text-center text-slate-400">
                    No activities found matching your criteria.
                  </td>
                </tr>
              ) : (
                activities.map((act) => {
                  const node = cpmMap[act.id] || cpmMap[act.activity_code];
                  const isCrit =
                    criticalCodes.has(act.activity_code) || (node && node.is_critical);
                  const isNegFloat =
                    negativeFloatCodes.has(act.activity_code) ||
                    (node && node.has_negative_float);
                  const floatDays = node && node.total_float !== undefined ? node.total_float : null;
                  const variance = computeVariance(act, node);

                  return (
                    <tr
                      key={act.id}
                      onClick={() => onEditActivity(act)}
                      className={`hover:bg-blue-50/50 cursor-pointer transition-colors group ${
                        isCrit
                          ? "border-l-2 border-l-rose-500 bg-rose-50/15"
                          : isNegFloat
                          ? "border-l-2 border-l-amber-500 bg-amber-50/15"
                          : ""
                      }`}
                    >
                      {/* Activity ID */}
                      <td className="px-3.5 py-2.5 font-bold text-slate-900 whitespace-nowrap font-mono text-xs">
                        {act.activity_code}
                      </td>

                      {/* Name */}
                      <td className="px-3.5 py-2.5 font-medium text-slate-800 max-w-xs truncate">
                        {act.name}
                      </td>

                      {/* WBS */}
                      <td className="px-3 py-2.5 text-slate-500 whitespace-nowrap font-mono text-[11px]">
                        {act.wbs_code || "-"}
                      </td>

                      {/* Status */}
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <StatusBadge status={act.status} size="xs" />
                      </td>

                      {/* Planned Start */}
                      <td className="px-3 py-2.5 whitespace-nowrap text-slate-600 font-mono text-[11px]">
                        {formatDate(act.planned_start)}
                      </td>

                      {/* Planned Finish */}
                      <td className="px-3 py-2.5 whitespace-nowrap text-slate-600 font-mono text-[11px]">
                        {formatDate(act.planned_finish)}
                      </td>

                      {/* Forecast / Actual Finish */}
                      <td className="px-3 py-2.5 whitespace-nowrap font-mono text-[11px]">
                        {act.status === "COMPLETED" && act.actual_finish ? (
                          <div className="flex items-center gap-1">
                            <span className="text-emerald-700 font-medium">
                              {formatDate(act.actual_finish)}
                            </span>
                            <span className="text-[9px] bg-emerald-50 text-emerald-700 border border-emerald-200 px-1 py-0.2 rounded font-sans font-semibold">
                              ACT
                            </span>
                          </div>
                        ) : node?.forecast_finish ? (
                          <div className="flex items-center gap-1">
                            <span className="text-slate-800 font-medium">
                              {formatDate(node.forecast_finish)}
                            </span>
                            <span className="text-[9px] bg-blue-50 text-blue-700 border border-blue-200 px-1 py-0.2 rounded font-sans">
                              FCST
                            </span>
                          </div>
                        ) : (
                          <span className="text-slate-400">
                            {formatDate(act.planned_finish)}
                          </span>
                        )}
                      </td>

                      {/* Finish Variance */}
                      <td className="px-3 py-2.5 whitespace-nowrap text-right font-mono text-[11px]">
                        <span
                          title="Finish variance between planned/baseline finish and current forecast or actual finish."
                          className={
                            variance.startsWith("+")
                              ? "text-rose-600 font-semibold"
                              : variance.startsWith("-")
                              ? "text-emerald-600 font-semibold"
                              : "text-slate-400"
                          }
                        >
                          {variance}
                        </span>
                      </td>

                      {/* Total Float */}
                      <td className="px-3 py-2.5 whitespace-nowrap text-right font-mono text-[11px]">
                        <div className="flex flex-col items-end">
                          {floatDays !== null ? (
                            <span
                              className={
                                floatDays < 0
                                  ? "font-bold text-rose-600"
                                  : floatDays === 0
                                  ? "font-semibold text-rose-700"
                                  : "text-slate-600"
                              }
                            >
                              {floatDays > 0 ? `+${floatDays}d` : `${floatDays}d`}
                            </span>
                          ) : (
                            <span className="text-slate-300">-</span>
                          )}
                          {node?.float_warning && (
                            <span
                              title={node.float_explanation || node.float_warning}
                              className={`text-[9px] px-1 py-0.2 rounded font-sans font-medium mt-0.5 cursor-help ${
                                node.float_warning === "HIGH_FLOAT"
                                  ? "bg-amber-100 text-amber-800 border border-amber-200"
                                  : node.float_warning === "OPEN_FINISH" ||
                                    node.float_warning === "OPEN_START"
                                  ? "bg-orange-100 text-orange-800 border border-orange-200"
                                  : node.float_warning === "DISCONNECTED"
                                  ? "bg-rose-100 text-rose-800 border border-rose-200"
                                  : "bg-slate-100 text-slate-700"
                              }`}
                            >
                              {node.float_warning === "HIGH_FLOAT"
                                ? "⚠ High Float"
                                : node.float_warning === "OPEN_FINISH"
                                ? "⚠ Open Finish"
                                : node.float_warning === "OPEN_START"
                                ? "⚠ Open Start"
                                : node.float_warning === "DISCONNECTED"
                                ? "⚠ Disconnected"
                                : "⚠ Notice"}
                            </span>
                          )}
                        </div>
                      </td>

                      {/* Criticality Indicator */}
                      <td className="px-2.5 py-2.5 whitespace-nowrap text-center">
                        {isCrit ? (
                          <span
                            title={
                              node?.driving_predecessor_code
                                ? `Critical Path Activity (Total Float ≤ 0). Driven by: ${node.driving_predecessor_code}`
                                : "Critical Path Activity (Total Float ≤ 0)"
                            }
                            className="inline-flex items-center justify-center h-4 w-4 rounded-full bg-rose-100 text-rose-700 text-[10px] font-bold cursor-help"
                          >
                            ●
                          </span>
                        ) : (
                          <span className="text-slate-200 text-xs">○</span>
                        )}
                      </td>

                      {/* Duration */}
                      <td className="px-3 py-2.5 whitespace-nowrap text-right font-mono text-[11px] text-slate-700">
                        {act.original_duration !== null ? `${act.original_duration}d` : "-"}
                      </td>

                      {/* % Complete */}
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-14 rounded-full bg-slate-100 overflow-hidden">
                            <div
                              className="h-full bg-blue-600 rounded-full"
                              style={{ width: `${act.percent_complete || 0}%` }}
                            />
                          </div>
                          <span className="font-mono text-[11px] text-slate-700">
                            {Math.round(act.percent_complete || 0)}%
                          </span>
                        </div>
                      </td>

                      {/* Actions */}
                      <td
                        className="px-3 py-2.5 text-right whitespace-nowrap"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => onEditActivity(act)}
                            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-blue-600 transition-colors"
                            title="Edit activity"
                          >
                            <Edit2 className="h-3.5 w-3.5" />
                          </button>
                          <button
                            onClick={() => handleDelete(act)}
                            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-600 transition-colors"
                            title="Delete activity"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Bar */}
        <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50/60 px-4 py-2.5 text-xs text-slate-500">
          <div>
            Showing <span className="font-semibold text-slate-800">{activities.length}</span> of{" "}
            <span className="font-semibold text-slate-800">{total}</span> activities
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              <span>Previous</span>
            </button>

            <span className="text-slate-600 font-mono text-[11px] px-1">
              Page {page} of {totalPages}
            </span>

            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <span>Next</span>
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
