"use client";

import React, { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  Search,
  FolderGit2,
  Activity as ActivityIcon,
  Layers,
  Calendar,
  FileSpreadsheet,
  Bot,
  Database,
  ArrowRight,
  X,
} from "lucide-react";
import { fetchProjects, fetchActivities } from "@/lib/api";
import { Project, Activity } from "@/lib/types";

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function CommandPalette({ isOpen, onClose }: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setLoading(true);
      fetchProjects()
        .then(async (projs) => {
          setProjects(projs);
          if (projs.length > 0) {
            // Load activities from first project for quick search
            const acts = await fetchActivities(projs[0].id, { page_size: 50 });
            setActivities(acts.items);
          }
        })
        .catch(console.error)
        .finally(() => setLoading(false));

      setTimeout(() => inputRef.current?.focus(), 50);
    } else {
      setQuery("");
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (isOpen) onClose();
        else onClose(); // parent handles toggle or we listen on window
      } else if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const q = query.toLowerCase().trim();

  const filteredProjects = projects.filter(
    (p) =>
      p.project_code.toLowerCase().includes(q) ||
      p.name.toLowerCase().includes(q)
  );

  const filteredActivities = activities.filter(
    (a) =>
      a.activity_code.toLowerCase().includes(q) ||
      a.name.toLowerCase().includes(q)
  );

  const navActions = [
    { label: "Overview / Control Center", tab: "overview", icon: ActivityIcon },
    { label: "Activities Spreadsheet", tab: "activities", icon: Layers },
    { label: "WBS Hierarchy Tree", tab: "wbs", icon: Layers },
    { label: "Gantt Timeline Chart", tab: "gantt", icon: Calendar },
    { label: "Field Reports & Review Center", tab: "reports", icon: FileSpreadsheet },
    { label: "Time Agent Assistant", tab: "agent", icon: Bot },
    { label: "Institutional Memory Analytics", tab: "memory", icon: Database },
  ].filter((a) => a.label.toLowerCase().includes(q));

  const handleSelectProject = (projectId: string, tab: string = "activities") => {
    onClose();
    router.push(`/projects/${projectId}?tab=${tab}`);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-16 sm:pt-24 bg-slate-900/40 backdrop-blur-xs px-4 animate-in fade-in duration-100">
      <div
        className="w-full max-w-2xl rounded-xl border border-slate-200 bg-white shadow-2xl overflow-hidden divide-y divide-slate-100 animate-in zoom-in-95 duration-100"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search Input Bar */}
        <div className="relative flex items-center px-4 py-3">
          <Search className="h-5 w-5 text-slate-400 shrink-0 mr-3" />
          <input
            ref={inputRef}
            type="text"
            placeholder="Search projects, activities, or commands... (e.g. BOROUGE4, CIV-1004, Gantt)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full bg-transparent text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none"
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="text-slate-400 hover:text-slate-600 p-1"
            >
              <X className="h-4 w-4" />
            </button>
          )}
          <span className="hidden sm:inline-block ml-2 rounded border border-slate-200 bg-slate-100 px-1.5 py-0.5 text-[10px] font-mono text-slate-500">
            ESC
          </span>
        </div>

        {/* Results Area */}
        <div className="max-h-96 overflow-y-auto p-2 space-y-4 text-xs">
          {/* Projects */}
          {filteredProjects.length > 0 && (
            <div>
              <div className="px-2 py-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                Projects
              </div>
              <div className="space-y-0.5 mt-1">
                {filteredProjects.map((p) => (
                  <div
                    key={p.id}
                    onClick={() => handleSelectProject(p.id)}
                    className="flex items-center justify-between px-3 py-2 rounded-md hover:bg-blue-50 cursor-pointer group transition-colors"
                  >
                    <div className="flex items-center gap-2.5">
                      <FolderGit2 className="h-4 w-4 text-blue-600 shrink-0" />
                      <div>
                        <span className="font-mono font-bold text-slate-900 mr-2">
                          {p.project_code}
                        </span>
                        <span className="text-slate-600">{p.name}</span>
                      </div>
                    </div>
                    <ArrowRight className="h-3.5 w-3.5 text-slate-300 group-hover:text-blue-600 transition-transform group-hover:translate-x-0.5" />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Quick Navigation Commands */}
          {projects.length > 0 && navActions.length > 0 && (
            <div>
              <div className="px-2 py-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                Navigate Active Project ({projects[0].project_code})
              </div>
              <div className="space-y-0.5 mt-1">
                {navActions.map((action, i) => (
                  <div
                    key={i}
                    onClick={() => handleSelectProject(projects[0].id, action.tab)}
                    className="flex items-center justify-between px-3 py-2 rounded-md hover:bg-slate-50 cursor-pointer group transition-colors"
                  >
                    <div className="flex items-center gap-2.5">
                      <action.icon className="h-4 w-4 text-slate-500 group-hover:text-blue-600 shrink-0" />
                      <span className="font-medium text-slate-800">{action.label}</span>
                    </div>
                    <ArrowRight className="h-3.5 w-3.5 text-slate-300 group-hover:text-slate-600 transition-transform group-hover:translate-x-0.5" />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Activities */}
          {filteredActivities.length > 0 && (
            <div>
              <div className="px-2 py-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">
                Activities
              </div>
              <div className="space-y-0.5 mt-1">
                {filteredActivities.slice(0, 8).map((act) => (
                  <div
                    key={act.id}
                    onClick={() => handleSelectProject(act.project_id, "activities")}
                    className="flex items-center justify-between px-3 py-2 rounded-md hover:bg-blue-50 cursor-pointer group transition-colors"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <ActivityIcon className="h-4 w-4 text-slate-400 shrink-0" />
                      <div className="truncate">
                        <span className="font-mono font-bold text-slate-900 mr-2">
                          {act.activity_code}
                        </span>
                        <span className="text-slate-600 truncate">{act.name}</span>
                      </div>
                    </div>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600 font-medium shrink-0 ml-2">
                      {act.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!loading &&
            filteredProjects.length === 0 &&
            filteredActivities.length === 0 &&
            navActions.length === 0 && (
              <div className="py-8 text-center text-slate-400">
                No matching projects or activities found.
              </div>
            )}
        </div>

        {/* Footer shortcuts */}
        <div className="flex items-center justify-between px-4 py-2 bg-slate-50 text-[11px] text-slate-500">
          <div className="flex items-center gap-3">
            <span>
              <kbd className="rounded border border-slate-300 bg-white px-1 py-0.5 text-[10px]">↑</kbd>
              <kbd className="rounded border border-slate-300 bg-white px-1 py-0.5 text-[10px] ml-1">↓</kbd> navigate
            </span>
            <span>
              <kbd className="rounded border border-slate-300 bg-white px-1 py-0.5 text-[10px]">↵</kbd> select
            </span>
          </div>
          <span>Primavera Platform Command Palette</span>
        </div>
      </div>
    </div>
  );
}
