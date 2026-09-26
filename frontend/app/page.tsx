"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import {
  Plus,
  Calendar,
  Layers,
  Activity as ActivityIcon,
  GitFork,
  Trash2,
  FolderGit2,
  ArrowUpRight,
  Clock,
  CheckCircle,
} from "lucide-react";
import { fetchProjects, deleteProject } from "@/lib/api";
import { Project } from "@/lib/types";
import ImportModal from "@/components/ImportModal";
import MetricCard from "@/components/ui/MetricCard";
import StatusBadge from "@/components/ui/StatusBadge";
import EmptyState from "@/components/ui/EmptyState";

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isImportOpen, setIsImportOpen] = useState(false);

  const loadProjects = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchProjects();
      setProjects(data);
    } catch (err: any) {
      console.error("Failed to load projects:", err);
      setError(err?.message || "Failed to load projects from backend API.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadProjects();
  }, []);

  const handleDelete = async (e: React.MouseEvent, project: Project) => {
    e.preventDefault();
    e.stopPropagation();

    if (
      confirm(
        `Are you sure you want to delete project '${project.project_code} - ${project.name}'?\nThis will permanently delete all associated WBS, activities, and relationships from PostgreSQL.`
      )
    ) {
      try {
        await deleteProject(project.id);
        loadProjects();
      } catch (err: any) {
        alert(err.message || "Failed to delete project.");
      }
    }
  };

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

  const totalActivities = projects.reduce((acc, p) => acc + (p.activity_count || 0), 0);
  const totalWBS = projects.reduce((acc, p) => acc + (p.wbs_count || 0), 0);
  const totalRels = projects.reduce((acc, p) => acc + (p.relationship_count || 0), 0);

  return (
    <div className="space-y-6">
      {/* Top Banner & Primary Action */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200/80 pb-6">
        <div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">
            Schedule Management
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 mt-1 max-w-2xl leading-relaxed">
            Enterprise schedule controls, deterministic CPM analytics, and field execution updates across Primavera P6 XER, XML, CSV, and XLSX formats.
          </p>
        </div>

        <button
          onClick={() => setIsImportOpen(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-xs hover:bg-blue-500 transition-all hover:shadow-sm self-start sm:self-auto shrink-0"
        >
          <Plus className="h-4 w-4" />
          Import Schedule File
        </button>
      </div>

      {/* Summary KPI Cards using Reusable MetricCard */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Active Projects"
          value={projects.length}
          subtext="Stored in PostgreSQL database"
          icon={FolderGit2}
          tone="primary"
        />

        <MetricCard
          label="Total Activities"
          value={totalActivities}
          subtext="Across all project schedules"
          icon={ActivityIcon}
          tone="success"
        />

        <MetricCard
          label="WBS Elements"
          value={totalWBS}
          subtext="Hierarchical packages"
          icon={Layers}
          tone="intelligence"
        />

        <MetricCard
          label="Logic Links"
          value={totalRels}
          subtext="Relationships (FS/SS/FF/SF)"
          icon={GitFork}
          tone="warning"
        />
      </div>

      {/* Projects List */}
      <div className="space-y-4 pt-2">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-slate-900">Projects Directory</h2>
          <span className="text-xs text-slate-400 font-medium">
            {projects.length} {projects.length === 1 ? "project" : "projects"} loaded
          </span>
        </div>

        {loading ? (
          <div className="rounded-lg border border-slate-200 bg-white p-12 text-center text-xs text-slate-400">
            <div className="inline-block animate-spin rounded-full h-5 w-5 border-2 border-blue-600 border-t-transparent mb-2"></div>
            <div>Loading schedules from PostgreSQL database...</div>
          </div>
        ) : error ? (
          <div className="rounded-lg border border-red-200 bg-red-50/50 p-8 text-center text-xs text-red-600 space-y-3">
            <p className="font-semibold text-sm">Failed to connect to Primavera Schedule Management API</p>
            <p className="text-slate-600 font-mono text-[11px] max-w-lg mx-auto">{error}</p>
            <button
              onClick={() => loadProjects()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 text-white rounded-md text-xs font-medium hover:bg-blue-700 transition"
            >
              Retry Connection
            </button>
          </div>
        ) : projects.length === 0 ? (
          <EmptyState
            icon={Calendar}
            title="No schedules imported yet"
            description="Get started by importing a Primavera .xer, .xml, .csv, or .xlsx schedule file."
            action={{
              label: "Import First Schedule",
              onClick: () => setIsImportOpen(true),
              icon: Plus,
            }}
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {projects.map((proj) => (
              <Link
                key={proj.id}
                href={`/projects/${proj.id}`}
                className="group relative flex flex-col justify-between rounded-lg border border-slate-200 bg-white p-5 shadow-2xs hover:border-blue-400 hover:shadow-xs transition-all"
              >
                <div>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-bold text-blue-700 bg-blue-50 px-2 py-0.5 rounded border border-blue-200/80">
                          {proj.project_code}
                        </span>
                        <StatusBadge status="ACTIVE" size="xs" />
                      </div>
                      <h3 className="mt-2 text-base font-bold text-slate-900 group-hover:text-blue-600 transition-colors truncate">
                        {proj.name || proj.project_code}
                      </h3>
                    </div>

                    <button
                      onClick={(e) => handleDelete(e, proj)}
                      className="text-slate-300 hover:text-rose-600 p-1 rounded transition-colors shrink-0"
                      title="Delete project"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>

                  {/* Dates */}
                  <div className="mt-4 space-y-1.5 text-xs text-slate-600 bg-slate-50 p-2.5 rounded border border-slate-100 font-mono">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400 text-[11px] font-sans">Planned Window:</span>
                      <span className="font-medium text-slate-800 text-[11px] truncate">
                        {formatDate(proj.planned_start)} — {formatDate(proj.planned_finish)}
                      </span>
                    </div>
                    {proj.data_date && (
                      <div className="flex items-center justify-between border-t border-slate-200/60 pt-1">
                        <span className="text-slate-400 text-[11px] font-sans">Data Date:</span>
                        <span className="font-medium text-slate-800 text-[11px]">
                          {formatDate(proj.data_date)}
                        </span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Metrics Footer */}
                <div className="mt-5 border-t border-slate-100 pt-3">
                  <div className="grid grid-cols-3 gap-2 text-center text-xs">
                    <div className="rounded bg-slate-50 p-1.5 border border-slate-100">
                      <div className="font-bold text-slate-900 font-mono">{proj.activity_count || 0}</div>
                      <div className="text-[10px] text-slate-500 uppercase tracking-tight">Activities</div>
                    </div>
                    <div className="rounded bg-slate-50 p-1.5 border border-slate-100">
                      <div className="font-bold text-slate-900 font-mono">{proj.wbs_count || 0}</div>
                      <div className="text-[10px] text-slate-500 uppercase tracking-tight">WBS</div>
                    </div>
                    <div className="rounded bg-slate-50 p-1.5 border border-slate-100">
                      <div className="font-bold text-slate-900 font-mono">{proj.relationship_count || 0}</div>
                      <div className="text-[10px] text-slate-500 uppercase tracking-tight">Links</div>
                    </div>
                  </div>

                  <div className="mt-3 flex items-center justify-end text-xs font-semibold text-blue-600 group-hover:translate-x-0.5 transition-transform">
                    <span>Open Workspace</span>
                    <ArrowUpRight className="ml-1 h-3.5 w-3.5" />
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Import Modal */}
      <ImportModal
        isOpen={isImportOpen}
        onClose={() => setIsImportOpen(false)}
        onImportSuccess={() => loadProjects()}
      />
    </div>
  );
}
