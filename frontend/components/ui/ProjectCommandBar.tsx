"use client";

import React, { useState } from "react";
import { Project } from "@/lib/types";
import { updateProjectDataDate } from "@/lib/api";
import { Calendar, GitFork, Layers, Activity as ActivityIcon, Edit2, Check, X } from "lucide-react";

interface ProjectCommandBarProps {
  project: Project;
  averageCompletion?: number;
  className?: string;
  onProjectUpdated?: () => void;
}

export default function ProjectCommandBar({
  project,
  averageCompletion = 0,
  className = "",
  onProjectUpdated,
}: ProjectCommandBarProps) {
  const [isEditingDataDate, setIsEditingDataDate] = useState(false);
  const [dataDateInput, setDataDateInput] = useState(
    project.data_date ? project.data_date.slice(0, 10) : ""
  );
  const [isSaving, setIsSaving] = useState(false);

  const formatDate = (val: string | null) => {
    if (!val) return "Not set";
    try {
      return new Date(val).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return val;
    }
  };

  const handleSaveDataDate = async () => {
    if (!dataDateInput) return;
    setIsSaving(true);
    try {
      await updateProjectDataDate(project.id, dataDateInput);
      setIsEditingDataDate(false);
      onProjectUpdated?.();
    } catch (err: any) {
      alert(err.message || "Failed to update project data date.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-5 shadow-2xs ${className}`}>
      {/* Top row: Project Code, Status, Schedule Window & Data Date */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 border-b border-slate-100 pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="font-mono text-xs font-bold text-blue-700 bg-blue-50 px-2 py-0.5 rounded border border-blue-200/80">
              {project.project_code}
            </span>
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200/80">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              ACTIVE
            </span>
          </div>
          <h1 className="mt-2 text-xl sm:text-2xl font-bold tracking-tight text-slate-900">
            {project.name || project.project_code}
          </h1>
        </div>

        {/* Schedule Window & Data Date */}
        <div className="grid grid-cols-2 gap-4 text-xs shrink-0 bg-slate-50 p-2.5 rounded-md border border-slate-200/60">
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] uppercase font-semibold text-slate-400">Data Date</span>
              {!isEditingDataDate && (
                <button
                  onClick={() => {
                    setDataDateInput(project.data_date ? project.data_date.slice(0, 10) : "");
                    setIsEditingDataDate(true);
                  }}
                  className="text-slate-400 hover:text-blue-600 transition-colors"
                  title="Change Data Date"
                >
                  <Edit2 className="h-3 w-3" />
                </button>
              )}
            </div>

            {isEditingDataDate ? (
              <div className="mt-1 flex items-center gap-1">
                <input
                  type="date"
                  value={dataDateInput}
                  onChange={(e) => setDataDateInput(e.target.value)}
                  className="rounded border border-slate-300 bg-white px-1.5 py-0.5 text-xs text-slate-800 font-mono focus:border-blue-500 focus:outline-none"
                />
                <button
                  onClick={handleSaveDataDate}
                  disabled={isSaving || !dataDateInput}
                  className="rounded bg-blue-600 p-1 text-white hover:bg-blue-500 disabled:opacity-50"
                  title="Save Data Date"
                >
                  <Check className="h-3 w-3" />
                </button>
                <button
                  onClick={() => setIsEditingDataDate(false)}
                  className="rounded bg-slate-200 p-1 text-slate-600 hover:bg-slate-300"
                  title="Cancel"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ) : (
              <div className="font-medium text-slate-800 mt-0.5 font-mono flex items-center gap-1.5">
                {project.data_date ? (
                  <span>{formatDate(project.data_date)}</span>
                ) : (
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-400 italic">Not set</span>
                    <button
                      onClick={() => {
                        setDataDateInput(new Date().toISOString().slice(0, 10));
                        setIsEditingDataDate(true);
                      }}
                      className="text-[10px] font-sans font-semibold text-blue-600 hover:underline"
                    >
                      [Set Data Date]
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="border-l border-slate-200 pl-4">
            <div className="text-[11px] uppercase font-semibold text-slate-400">Schedule Window</div>
            <div className="font-medium text-slate-800 mt-0.5 font-mono truncate">
              {formatDate(project.planned_start)} — {formatDate(project.planned_finish)}
            </div>
          </div>
        </div>
      </div>

      {/* Bottom row: Dynamic Stats Strip */}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-600">
        <div className="flex flex-wrap items-center gap-4 sm:gap-6">
          <div className="flex items-center gap-1.5">
            <ActivityIcon className="h-3.5 w-3.5 text-blue-600" />
            <span className="font-bold text-slate-900 font-mono">{project.activity_count || 0}</span>
            <span className="text-slate-500">Activities</span>
          </div>

          <span className="text-slate-300">•</span>

          <div className="flex items-center gap-1.5">
            <Layers className="h-3.5 w-3.5 text-indigo-600" />
            <span className="font-bold text-slate-900 font-mono">{project.wbs_count || 0}</span>
            <span className="text-slate-500">WBS Nodes</span>
          </div>

          <span className="text-slate-300">•</span>

          <div className="flex items-center gap-1.5">
            <GitFork className="h-3.5 w-3.5 text-amber-600" />
            <span className="font-bold text-slate-900 font-mono">{project.relationship_count || 0}</span>
            <span className="text-slate-500">Links</span>
          </div>
        </div>

        {/* Progress bar pill */}
        <div className="flex items-center gap-3">
          <div className="text-right">
            <span className="font-bold text-slate-900 font-mono">{averageCompletion}%</span>{" "}
            <span className="text-slate-500">Complete</span>
          </div>
          <div className="h-2 w-24 rounded-full bg-slate-100 overflow-hidden">
            <div
              className="h-full rounded-full bg-blue-600 transition-all duration-300"
              style={{ width: `${Math.min(100, Math.max(0, averageCompletion))}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
