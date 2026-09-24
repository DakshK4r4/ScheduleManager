"use client";

import React from "react";
import { Project } from "@/lib/types";
import { Calendar, GitFork, Layers, Activity as ActivityIcon } from "lucide-react";

interface ProjectCommandBarProps {
  project: Project;
  averageCompletion?: number;
  className?: string;
}

export default function ProjectCommandBar({
  project,
  averageCompletion = 0,
  className = "",
}: ProjectCommandBarProps) {
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
            <div className="text-[11px] uppercase font-semibold text-slate-400">Data Date</div>
            <div className="font-medium text-slate-800 mt-0.5 font-mono">
              {formatDate(project.data_date)}
            </div>
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
