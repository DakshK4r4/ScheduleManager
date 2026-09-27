"use client";

import React, { useState, useEffect, Suspense } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Calendar,
  Layers,
  Table as TableIcon,
  BarChart3,
  Clock,
  CheckCircle2,
  AlertCircle,
  FileSpreadsheet,
  Bot,
  Database,
  GitFork,
  ArrowRight,
  ShieldAlert,
  Activity as ActivityIcon,
  Check,
  RefreshCw,
} from "lucide-react";
import {
  fetchProject,
  fetchActivities,
  fetchProjectCPM,
  fetchScheduleHealth,
  fetchReviewQueue,
  fetchProjectArtifacts,
  fetchAuditTrail,
  isValidProjectId,
} from "@/lib/api";
import { Activity, Project, CPMResult, ScheduleHealthResult, Artifact, AuditLogItem } from "@/lib/types";
import ActivityTable from "@/components/ActivityTable";
import ActivityEditorModal from "@/components/ActivityEditorModal";
import WbsTree from "@/components/WbsTree";
import GanttChart from "@/components/GanttChart";
import FieldReportsAndReview from "@/components/FieldReportsAndReview";
import TimeAgentChat from "@/components/TimeAgentChat";
import InstitutionalMemoryWorkspace from "@/components/institutional-memory/InstitutionalMemoryWorkspace";
import Breadcrumbs from "@/components/ui/Breadcrumbs";
import ProjectCommandBar from "@/components/ui/ProjectCommandBar";
import MetricCard from "@/components/ui/MetricCard";
import StatusBadge from "@/components/ui/StatusBadge";
import EmptyState from "@/components/ui/EmptyState";

type ActiveTab = "overview" | "activities" | "wbs" | "gantt" | "reports" | "agent" | "memory";

type ProjectLoadingState =
  | "INITIALIZING"
  | "RESOLVING_PROJECT_ID"
  | "LOADING_PROJECT"
  | "PROJECT_READY"
  | "PROJECT_NOT_FOUND"
  | "PROJECT_LOAD_ERROR"
  | "INVALID_PROJECT_ID";

function ProjectWorkspaceContent() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  // Authoritative canonical route identifier from URL segment
  const rawId = params?.id;
  const candidateId = Array.isArray(rawId) ? rawId[0] : (rawId as string | undefined);
  const projectId = (candidateId || "") as string;

  const [loadingState, setLoadingState] = useState<ProjectLoadingState>("INITIALIZING");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [activeTab, setActiveTab] = useState<ActiveTab>("overview");

  // CPM & Schedule Health Data
  const [cpmData, setCpmData] = useState<CPMResult | null>(null);
  const [cpmLoading, setCpmLoading] = useState(false);
  const [healthData, setHealthData] = useState<ScheduleHealthResult | null>(null);

  // Execution Telemetry
  const [pendingReviewCount, setPendingReviewCount] = useState(0);
  const [recentArtifacts, setRecentArtifacts] = useState<Artifact[]>([]);
  const [recentAudits, setRecentAudits] = useState<AuditLogItem[]>([]);

  // Activity editor modal state
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editingActivity, setEditingActivity] = useState<Activity | null>(null);
  const [refreshCounter, setRefreshCounter] = useState(0);

  // Overview status metrics
  const [statusCounts, setStatusCounts] = useState({
    notStarted: 0,
    inProgress: 0,
    completed: 0,
    avgPercent: 0,
  });

  // Sync tab from query param if available
  useEffect(() => {
    const tabParam = searchParams.get("tab") as ActiveTab;
    if (
      tabParam &&
      ["overview", "activities", "wbs", "gantt", "reports", "agent", "memory"].includes(tabParam)
    ) {
      setActiveTab(tabParam);
    }
  }, [searchParams]);

  // Authoritative project data loading with lifecycle and cancellation
  useEffect(() => {
    // 1. Resolve project ID from URL parameter
    if (!candidateId) {
      setLoadingState("RESOLVING_PROJECT_ID");
      return;
    }

    // 2. Validate canonical project ID
    if (!isValidProjectId(candidateId)) {
      setLoadingState("INVALID_PROJECT_ID");
      setErrorMessage(`The project identifier "${candidateId}" is invalid or malformed.`);
      setProject(null);
      return;
    }

    // 3. Initiate loading with AbortController for race condition & StrictMode protection
    const abortController = new AbortController();
    let isMounted = true;

    setLoadingState("LOADING_PROJECT");
    setErrorMessage(null);
    setProject(null);

    async function loadData() {
      try {
        const proj = await fetchProject(candidateId!, { signal: abortController.signal });
        if (!isMounted) return;

        setProject(proj);
        setLoadingState("PROJECT_READY");

        // Load activities to compute breakdown (non-blocking)
        try {
          const acts = await fetchActivities(candidateId!, { page_size: 1000 }, { signal: abortController.signal });
          if (isMounted) {
            let ns = 0,
              ip = 0,
              comp = 0,
              totalPct = 0;
            acts.items.forEach((a) => {
              if (a.status === "COMPLETED") comp++;
              else if (a.status === "IN_PROGRESS") ip++;
              else ns++;
              totalPct += a.percent_complete || 0;
            });

            const avg = acts.items.length > 0 ? Math.round(totalPct / acts.items.length) : 0;
            setStatusCounts({
              notStarted: ns,
              inProgress: ip,
              completed: comp,
              avgPercent: avg,
            });
          }
        } catch (actErr: any) {
          if (actErr?.name !== "AbortError") console.warn("Activity metrics warning:", actErr);
        }

        // Load CPM schedule health (non-blocking)
        try {
          setCpmLoading(true);
          const [cpm, health] = await Promise.all([
            fetchProjectCPM(candidateId!, { signal: abortController.signal }),
            fetchScheduleHealth(candidateId!, { signal: abortController.signal }).catch(() => null),
          ]);
          if (isMounted) {
            setCpmData(cpm);
            if (health) setHealthData(health);
          }
        } catch (cpmErr: any) {
          if (cpmErr?.name !== "AbortError") console.warn("CPM calculation warning:", cpmErr);
        } finally {
          if (isMounted) setCpmLoading(false);
        }

        // Load execution telemetry (non-blocking)
        try {
          const [qRes, artRes, auditRes] = await Promise.all([
            fetchReviewQueue(candidateId!, { signal: abortController.signal }),
            fetchProjectArtifacts(candidateId!, { signal: abortController.signal }),
            fetchAuditTrail(candidateId!, { signal: abortController.signal }),
          ]);
          if (isMounted) {
            setPendingReviewCount(qRes.pending_count || 0);
            setRecentArtifacts(artRes.slice(0, 5));
            setRecentAudits(auditRes.audit_trail ? auditRes.audit_trail.slice(0, 5) : []);
          }
        } catch (telemetryErr: any) {
          if (telemetryErr?.name !== "AbortError") console.warn("Execution telemetry warning:", telemetryErr);
        }
      } catch (err: any) {
        if (err?.name === "AbortError" || !isMounted) return;
        console.error("Failed to load project details:", err);
        if (err?.status === 404) {
          setLoadingState("PROJECT_NOT_FOUND");
          setErrorMessage("The requested schedule project could not be located in PostgreSQL.");
        } else if (err?.status === 400 || err?.status === 422) {
          setLoadingState("INVALID_PROJECT_ID");
          setErrorMessage(err?.message || `Invalid project identifier: "${candidateId}".`);
        } else {
          setLoadingState("PROJECT_LOAD_ERROR");
          setErrorMessage(
            err?.message || "Unable to connect to the schedule database service. Please check your network or retry."
          );
        }
      }
    }

    loadData();

    return () => {
      isMounted = false;
      abortController.abort();
    };
  }, [candidateId, refreshCounter]);

  const handleEditActivity = (act: Activity) => {
    setEditingActivity(act);
    setIsEditorOpen(true);
  };

  const handleAddActivity = () => {
    setEditingActivity(null);
    setIsEditorOpen(true);
  };

  const handleSaved = () => {
    setRefreshCounter((c) => c + 1);
  };

  const tabLabels: Record<ActiveTab, string> = {
    overview: "Overview & Health",
    activities: "Activities",
    wbs: "WBS Hierarchy",
    gantt: "Gantt Timeline",
    reports: "Field Execution",
    agent: "Time Agent",
    memory: "Institutional Memory",
  };

  if (
    loadingState === "INITIALIZING" ||
    loadingState === "RESOLVING_PROJECT_ID" ||
    loadingState === "LOADING_PROJECT"
  ) {
    return (
      <div className="py-24 text-center space-y-3">
        <RefreshCw className="mx-auto h-8 w-8 text-blue-600 animate-spin" />
        <h3 className="text-sm font-semibold text-slate-800">
          {loadingState === "RESOLVING_PROJECT_ID"
            ? "Resolving project identifier..."
            : "Loading project workspace & schedule telemetry..."}
        </h3>
        <p className="text-xs text-slate-500">
          Connecting to PostgreSQL and synchronizing schedule state...
        </p>
      </div>
    );
  }

  if (loadingState === "INVALID_PROJECT_ID") {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-8 text-center space-y-3 max-w-lg mx-auto my-12">
        <AlertCircle className="mx-auto h-8 w-8 text-amber-600" />
        <h3 className="text-base font-bold text-amber-900">Invalid Project Identifier</h3>
        <p className="text-xs text-amber-700">
          {errorMessage || "The project ID in the URL is invalid or malformed."}
        </p>
        <div className="pt-2">
          <Link
            href="/"
            className="inline-block rounded-md bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-500 shadow-xs"
          >
            Return to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  if (loadingState === "PROJECT_NOT_FOUND") {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 p-8 text-center space-y-3 max-w-lg mx-auto my-12">
        <AlertCircle className="mx-auto h-8 w-8 text-rose-600" />
        <h3 className="text-base font-bold text-rose-900">Project Not Found</h3>
        <p className="text-xs text-rose-700">The requested schedule project could not be located in PostgreSQL.</p>
        <div className="pt-2">
          <Link
            href="/"
            className="inline-block rounded-md bg-rose-600 px-4 py-2 text-xs font-semibold text-white hover:bg-rose-500 shadow-xs"
          >
            Return to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  if (loadingState === "PROJECT_LOAD_ERROR" || !project) {
    return (
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-8 text-center space-y-3 max-w-lg mx-auto my-12">
        <AlertCircle className="mx-auto h-8 w-8 text-slate-600" />
        <h3 className="text-base font-bold text-slate-900">Unable to Load Project</h3>
        <p className="text-xs text-slate-600">
          {errorMessage || "A temporary database connection or network error occurred while loading this project."}
        </p>
        <div className="pt-2 flex items-center justify-center gap-3">
          <button
            onClick={() => setRefreshCounter((c) => c + 1)}
            className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-500 shadow-xs cursor-pointer"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Retry Connection
          </button>
          <Link
            href="/"
            className="inline-block rounded-md border border-slate-300 bg-white px-4 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-100"
          >
            Return to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  const criticalCount = cpmData?.critical_activities?.length || 0;
  const negativeFloatCount = cpmData?.negative_float_activities?.length || 0;
  const nearCriticalCount = cpmData?.near_critical_activities?.length || 0;
  const openEndedCount = (cpmData?.open_start_activities?.length || 0) + (cpmData?.open_finish_activities?.length || 0);

  return (
    <div className="space-y-5">
      {/* 1. Contextual Breadcrumbs */}
      <Breadcrumbs
        items={[
          { label: project.project_code, href: `/projects/${project.id}?tab=overview` },
          { label: tabLabels[activeTab], active: true },
        ]}
        backHref="/"
        backLabel="Back to Projects"
      />

      {/* 2. Professional Project Command Bar */}
      <ProjectCommandBar
        project={project}
        averageCompletion={statusCounts.avgPercent}
        onProjectUpdated={() => setRefreshCounter((c) => c + 1)}
      />

      {/* 3. Module Navigation Tabs */}
      <div className="border-b border-slate-200/80 bg-white px-2 rounded-t-lg">
        <nav className="flex space-x-1 sm:space-x-4 overflow-x-auto">
          <button
            onClick={() => setActiveTab("overview")}
            className={`flex items-center gap-2 py-3 px-3 text-xs font-semibold border-b-2 transition-all whitespace-nowrap ${
              activeTab === "overview"
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-slate-500 hover:text-slate-900 hover:border-slate-300"
            }`}
          >
            <BarChart3 className="h-4 w-4" />
            <span>Control Center</span>
          </button>

          <button
            onClick={() => setActiveTab("activities")}
            className={`flex items-center gap-2 py-3 px-3 text-xs font-semibold border-b-2 transition-all whitespace-nowrap ${
              activeTab === "activities"
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-slate-500 hover:text-slate-900 hover:border-slate-300"
            }`}
          >
            <TableIcon className="h-4 w-4" />
            <span>Activities</span>
            <span className="rounded-full bg-slate-100 px-1.5 py-0.2 text-[10px] font-mono text-slate-600">
              {project.activity_count || 0}
            </span>
          </button>

          <button
            onClick={() => setActiveTab("wbs")}
            className={`flex items-center gap-2 py-3 px-3 text-xs font-semibold border-b-2 transition-all whitespace-nowrap ${
              activeTab === "wbs"
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-slate-500 hover:text-slate-900 hover:border-slate-300"
            }`}
          >
            <Layers className="h-4 w-4" />
            <span>WBS Tree</span>
            <span className="rounded-full bg-slate-100 px-1.5 py-0.2 text-[10px] font-mono text-slate-600">
              {project.wbs_count || 0}
            </span>
          </button>

          <button
            onClick={() => setActiveTab("gantt")}
            className={`flex items-center gap-2 py-3 px-3 text-xs font-semibold border-b-2 transition-all whitespace-nowrap ${
              activeTab === "gantt"
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-slate-500 hover:text-slate-900 hover:border-slate-300"
            }`}
          >
            <Calendar className="h-4 w-4" />
            <span>Gantt Timeline</span>
          </button>

          <button
            onClick={() => setActiveTab("reports")}
            className={`flex items-center gap-2 py-3 px-3 text-xs font-semibold border-b-2 transition-all whitespace-nowrap ${
              activeTab === "reports"
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-slate-500 hover:text-slate-900 hover:border-slate-300"
            }`}
          >
            <FileSpreadsheet className="h-4 w-4" />
            <span>Field Execution</span>
            {pendingReviewCount > 0 && (
              <span className="rounded-full bg-amber-500 text-white px-1.5 py-0.2 text-[10px] font-bold">
                {pendingReviewCount}
              </span>
            )}
          </button>

          <button
            onClick={() => setActiveTab("agent")}
            className={`flex items-center gap-2 py-3 px-3 text-xs font-semibold border-b-2 transition-all whitespace-nowrap ${
              activeTab === "agent"
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-slate-500 hover:text-slate-900 hover:border-slate-300"
            }`}
          >
            <Bot className="h-4 w-4" />
            <span>Time Agent</span>
          </button>

          <button
            onClick={() => setActiveTab("memory")}
            className={`flex items-center gap-2 py-3 px-3 text-xs font-semibold border-b-2 transition-all whitespace-nowrap ${
              activeTab === "memory"
                ? "border-indigo-600 text-indigo-600 font-bold"
                : "border-transparent text-slate-500 hover:text-slate-900 hover:border-slate-300"
            }`}
          >
            <Database className="h-4 w-4" />
            <span>Institutional Memory</span>
          </button>
        </nav>
      </div>

      {/* 4. Tab Content */}

      {/* TAB 1: OVERVIEW -> PROJECT CONTROL CENTER */}
      {activeTab === "overview" && (
        <div className="space-y-6">
          {/* Top KPI Cards Strip */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard
              label="Schedule Progress"
              value={`${statusCounts.avgPercent}%`}
              subtext={`${statusCounts.completed} completed, ${statusCounts.inProgress} in progress`}
              progressPercent={statusCounts.avgPercent}
              icon={ActivityIcon}
              tone="primary"
            />

            <MetricCard
              label="Completed Activities"
              value={statusCounts.completed}
              subtext="Finished tasks (100% complete)"
              icon={CheckCircle2}
              tone="success"
            />

            <MetricCard
              label="Critical Activities"
              value={criticalCount}
              subtext={criticalCount > 0 ? "Total Float ≤ 0 days (Longest Path)" : "Zero critical path delays"}
              icon={ShieldAlert}
              tone={criticalCount > 0 ? "danger" : "default"}
            />

            <MetricCard
              label="Negative Float"
              value={negativeFloatCount}
              subtext={negativeFloatCount > 0 ? "Activities behind target finish" : "Schedule targets met"}
              icon={Clock}
              tone={negativeFloatCount > 0 ? "danger" : "success"}
            />
          </div>

          {/* SCHEDULE HEALTH & LOGIC QUALITY SECTION */}
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-2xs space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-b border-slate-100 pb-3">
              <div>
                <h3 className="text-sm font-bold uppercase tracking-wider text-slate-900">
                  Schedule Health &amp; Logic Quality
                </h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  Deterministic Critical Path Method (CPM) analytics computed against live network logic
                </p>
              </div>

              <div className="flex items-center gap-3">
                {healthData && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs text-slate-500 font-medium">DCMA 14-Pt:</span>
                    <span className={`rounded-md px-2 py-0.5 text-xs font-bold font-mono border ${
                      healthData.grade === "A" ? "bg-emerald-50 text-emerald-800 border-emerald-200" :
                      healthData.grade === "B" ? "bg-blue-50 text-blue-800 border-blue-200" :
                      healthData.grade === "C" ? "bg-amber-50 text-amber-800 border-amber-200" :
                      "bg-rose-50 text-rose-800 border-rose-200"
                    }`}>
                      Grade {healthData.grade} ({healthData.health_score}/100)
                    </span>
                  </div>
                )}
                <div className="flex items-center gap-1.5">
                  <span className="text-xs text-slate-500 font-medium">Logic Quality:</span>
                  <span className="rounded-md bg-emerald-50 text-emerald-800 border border-emerald-200 px-2 py-0.5 text-xs font-bold font-mono">
                    {cpmData ? `${cpmData.logic_quality_percent}%` : "Calculating..."}
                  </span>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs">
              <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
                <div className="text-[11px] font-semibold uppercase text-slate-400">Critical Activities</div>
                <div className="mt-1 text-xl font-bold font-mono text-slate-900">{criticalCount}</div>
                <div className="text-[10px] text-slate-500 mt-0.5">Float ≤ 0 days</div>
              </div>

              <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
                <div className="text-[11px] font-semibold uppercase text-slate-400">Near Critical</div>
                <div className="mt-1 text-xl font-bold font-mono text-slate-900">{nearCriticalCount}</div>
                <div className="text-[10px] text-slate-500 mt-0.5">0 &lt; Float ≤ 5d</div>
              </div>

              <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
                <div className="text-[11px] font-semibold uppercase text-slate-400">Negative Float</div>
                <div className={`mt-1 text-xl font-bold font-mono ${negativeFloatCount > 0 ? "text-rose-600" : "text-slate-900"}`}>
                  {negativeFloatCount}
                </div>
                <div className="text-[10px] text-slate-500 mt-0.5">Target breached</div>
              </div>

              <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
                <div className="text-[11px] font-semibold uppercase text-slate-400">Open-Ended Logic</div>
                <div className="mt-1 text-xl font-bold font-mono text-slate-900">{openEndedCount}</div>
                <div className="text-[10px] text-slate-500 mt-0.5">Open start/finish</div>
              </div>

              <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
                <div className="text-[11px] font-semibold uppercase text-slate-400">Isolated Nodes</div>
                <div className="mt-1 text-xl font-bold font-mono text-slate-900">
                  {cpmData?.isolated_activities?.length || 0}
                </div>
                <div className="text-[10px] text-slate-500 mt-0.5">No predecessors</div>
              </div>

              <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
                <div className="text-[11px] font-semibold uppercase text-slate-400">Calculated Duration</div>
                <div className="mt-1 text-xl font-bold font-mono text-slate-900">
                  {cpmData ? `${cpmData.project_duration_days}d` : "-"}
                </div>
                <div className="text-[10px] text-slate-500 mt-0.5">Working days</div>
              </div>
            </div>

            {healthData && healthData.recommendations && healthData.recommendations.length > 0 && (
              <div className="pt-2 border-t border-slate-100">
                <div className="text-[11px] font-semibold text-slate-700 mb-1.5 flex items-center gap-1.5">
                  <ShieldAlert className="h-3.5 w-3.5 text-blue-600" />
                  <span>DCMA 14-Point Health Recommendations:</span>
                </div>
                <div className="space-y-1">
                  {healthData.recommendations.slice(0, 3).map((rec, i) => (
                    <div key={i} className="text-[11px] text-slate-600 flex items-start gap-1.5">
                      <span className="text-slate-400">•</span>
                      <span>{rec}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* CRITICAL PATH SEQUENCE */}
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-2xs space-y-3">
            <div className="flex items-center justify-between border-b border-slate-100 pb-2.5">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-bold uppercase tracking-wider text-slate-900">
                  Critical Path Driving Sequence
                </h3>
                <span className="rounded bg-rose-50 text-rose-700 border border-rose-200/80 px-2 py-0.5 text-[11px] font-bold">
                  {cpmData?.critical_path?.length || 0} activities
                </span>
              </div>
              <button
                onClick={() => setActiveTab("activities")}
                className="text-xs font-semibold text-blue-600 hover:text-blue-700 inline-flex items-center gap-1"
              >
                <span>View in Spreadsheet</span>
                <ArrowRight className="h-3 w-3" />
              </button>
            </div>

            {cpmLoading ? (
              <div className="py-6 text-center text-xs text-slate-400">Calculating CPM Critical Path...</div>
            ) : !cpmData || cpmData.critical_path.length === 0 ? (
              <div className="py-6 text-center text-xs text-slate-500">
                No continuous driving path detected (check for open dependencies or constraints).
              </div>
            ) : (
              <div className="overflow-x-auto py-2">
                <div className="flex items-center gap-2 min-w-max">
                  {cpmData.critical_path.slice(0, 10).map((actCode, idx) => (
                    <React.Fragment key={actCode}>
                      <div className="rounded-md border border-rose-200 bg-rose-50/60 p-2 text-center hover:bg-rose-100/70 transition-colors">
                        <div className="font-mono text-xs font-bold text-rose-900">{actCode}</div>
                        <div className="text-[10px] text-rose-700 mt-0.5">
                          {cpmData.activities[actCode]?.name || "Activity"}
                        </div>
                      </div>
                      {idx < Math.min(cpmData.critical_path.length - 1, 9) && (
                        <ArrowRight className="h-3.5 w-3.5 text-slate-300 shrink-0" />
                      )}
                    </React.Fragment>
                  ))}
                  {cpmData.critical_path.length > 10 && (
                    <span className="text-xs font-semibold text-slate-400 px-2">
                      +{cpmData.critical_path.length - 10} more
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* RECENT FIELD EXECUTION & VERIFICATION */}
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-2xs space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-2.5">
              <div>
                <h4 className="text-xs font-bold uppercase tracking-wider text-slate-900">
                  Field Reports &amp; Execution Verification
                </h4>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  Site evidence intake, automatic extraction &amp; pending supervisor review items
                </p>
              </div>
              <button
                onClick={() => setActiveTab("reports")}
                className="text-xs font-semibold text-blue-600 hover:text-blue-700"
              >
                Open Execution Center →
              </button>
            </div>

            {pendingReviewCount > 0 ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3.5 flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <AlertCircle className="h-5 w-5 text-amber-600 shrink-0" />
                  <div>
                    <div className="text-xs font-bold text-amber-900">
                      {pendingReviewCount} items awaiting verification
                    </div>
                    <p className="text-[11px] text-amber-700 mt-0.5">
                      Extracted field progress requires reviewer approval before applying to CPM.
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setActiveTab("reports")}
                  className="rounded bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500 shrink-0 shadow-2xs"
                >
                  Review Now
                </button>
              </div>
            ) : (
              <div className="rounded-md border border-slate-100 bg-slate-50 p-3 text-center text-xs text-slate-500">
                All field reports processed and verified.
              </div>
            )}

            <div className="space-y-2 pt-1">
              <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-tight">
                Recent Ingested Artifacts
              </div>
              {recentArtifacts.length === 0 ? (
                <p className="text-xs text-slate-400 py-2">No field reports uploaded yet.</p>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                  {recentArtifacts.slice(0, 6).map((art) => (
                    <div
                      key={art.artifact_id}
                      className="flex items-center justify-between text-xs py-2 px-2.5 rounded bg-slate-50 border border-slate-100 font-mono"
                    >
                      <span className="text-slate-700 truncate max-w-[180px] font-sans font-medium" title={art.original_filename}>
                        {art.original_filename}
                      </span>
                      <span className="text-[10px] text-slate-500 uppercase px-1.5 py-0.5 rounded bg-white border border-slate-200">
                        {art.extraction_status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: ACTIVITIES SPREADSHEET */}
      {activeTab === "activities" && (
        <ActivityTable
          projectId={projectId}
          onEditActivity={handleEditActivity}
          onAddActivity={handleAddActivity}
          refreshTrigger={refreshCounter}
        />
      )}

      {/* TAB 3: WBS TREE */}
      {activeTab === "wbs" && <WbsTree projectId={projectId} />}

      {/* TAB 4: GANTT CHART */}
      {activeTab === "gantt" && (
        <GanttChart
          projectId={projectId}
          projectDataDate={project.data_date}
          onEditActivity={handleEditActivity}
        />
      )}

      {/* TAB 5: FIELD REPORTS & REVIEW */}
      {activeTab === "reports" && (
        <FieldReportsAndReview
          projectId={projectId}
          onScheduleUpdated={() => setRefreshCounter((c) => c + 1)}
        />
      )}

      {/* TAB 6: TIME AGENT */}
      {activeTab === "agent" && (
        <TimeAgentChat
          projectId={projectId}
          projectName={project.name || project.project_code}
          onScheduleUpdated={() => setRefreshCounter((c) => c + 1)}
        />
      )}

      {/* TAB 7: INSTITUTIONAL MEMORY */}
      {activeTab === "memory" && (
        <InstitutionalMemoryWorkspace projectId={projectId} project={project} />
      )}

      {/* Activity Editor Modal */}
      <ActivityEditorModal
        isOpen={isEditorOpen}
        onClose={() => setIsEditorOpen(false)}
        onSaved={handleSaved}
        activity={editingActivity}
        projectId={projectId}
      />
    </div>
  );
}

export default function ProjectWorkspace() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-xs text-slate-400">Loading project workspace...</div>}>
      <ProjectWorkspaceContent />
    </Suspense>
  );
}
