"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  UploadCloud,
  FileText,
  CheckCircle2,
  AlertCircle,
  Clock,
  ExternalLink,
  RefreshCw,
  Sliders,
  Check,
  X,
  FileSpreadsheet,
  Mic,
  ShieldCheck,
  Download,
  AlertTriangle,
  ArrowRight,
  ChevronRight,
} from "lucide-react";
import {
  uploadFieldArtifact,
  fetchProjectArtifacts,
  extractArtifact,
  evaluateMatching,
  fetchReviewQueue,
  submitReviewDecision,
  fetchAuditTrail,
  getExportXerUrl,
} from "@/lib/api";
import { Artifact, ReviewQueueItem, AuditLogItem } from "@/lib/types";
import StatusBadge from "./ui/StatusBadge";
import EmptyState from "./ui/EmptyState";

interface FieldReportsAndReviewProps {
  projectId: string;
  onScheduleUpdated: () => void;
}

export default function FieldReportsAndReview({
  projectId,
  onScheduleUpdated,
}: FieldReportsAndReviewProps) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [queueItems, setQueueItems] = useState<ReviewQueueItem[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Upload state
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [reportId, setReportId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadSuccessMsg, setUploadSuccessMsg] = useState<string | null>(null);
  const [activeArtifactId, setActiveArtifactId] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  // Processing state
  const [extracting, setExtracting] = useState(false);
  const [matching, setMatching] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [autoProcess, setAutoProcess] = useState(true);

  // Review decision state for each item
  const [selectedCandidates, setSelectedCandidates] = useState<Record<string, string>>({});
  const [adjustPercents, setAdjustPercents] = useState<Record<string, string>>({});
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});
  const [submittingReview, setSubmittingReview] = useState<Record<string, boolean>>({});

  const fileInputRef = useRef<HTMLInputElement>(null);
  const reviewQueueRef = useRef<HTMLDivElement>(null);

  const refreshAll = async () => {
    setLoading(true);
    try {
      const [artList, qRes, auditRes] = await Promise.all([
        fetchProjectArtifacts(projectId),
        fetchReviewQueue(projectId),
        fetchAuditTrail(projectId),
      ]);
      setArtifacts(artList);
      setQueueItems(qRes.items);
      setAuditLogs(auditRes.audit_trail);
    } catch (err) {
      console.error("Failed to fetch integration data:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (projectId) {
      refreshAll();
    }
  }, [projectId]);

  const handleUpload = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!selectedFile) return;

    setUploading(true);
    setUploadSuccessMsg(null);
    setActionMessage(null);

    try {
      const res = await uploadFieldArtifact(projectId, selectedFile, reportId.trim() || undefined);
      const artId = res.artifact.artifact_id;
      setActiveArtifactId(artId);
      setSelectedFile(null);
      setReportId("");

      if (autoProcess) {
        setUploadSuccessMsg("Saved to MinIO. Extracting field events and evaluating CPM matches...");
        setExtracting(true);
        try {
          const extRes = await extractArtifact(artId);
          setMatching(true);
          const matchRes = await evaluateMatching(projectId);
          setActionMessage(
            `Pipeline Execution Complete: Extracted ${extRes.events_extracted} event(s) | ${matchRes.auto_linked_count} auto-linked to CPM, ${matchRes.review_queued_count} queued for review.`
          );
          setUploadSuccessMsg(
            `Artifact saved in MinIO (${res.artifact.original_filename}) • ${extRes.events_extracted} events extracted & matched!`
          );
          onScheduleUpdated();
        } catch (pipeErr: any) {
          setActionMessage(
            `Uploaded to MinIO, but automated extraction/matching encountered: ${pipeErr.message}. You can retry using the buttons below.`
          );
        } finally {
          setExtracting(false);
          setMatching(false);
        }
      } else {
        setUploadSuccessMsg(
          `${res.is_duplicate ? "Duplicate detected: " : "Success: "} ${res.message} (SHA-256: ${res.artifact.sha256.substring(0, 12)}...)`
        );
      }

      await refreshAll();
    } catch (err: any) {
      setActionMessage(`Upload failed: ${err.message}`);
    } finally {
      setUploading(false);
    }
  };

  const handleRunExtraction = async (artId: string, forceReextract: boolean = false) => {
    setExtracting(true);
    setActionMessage(null);
    try {
      const res = await extractArtifact(artId, forceReextract);
      setActionMessage(
        `Extraction complete: Extracted ${res.events_extracted} progress event(s) from document.`
      );
      await refreshAll();
    } catch (err: any) {
      setActionMessage(`Extraction failed: ${err.message}`);
    } finally {
      setExtracting(false);
    }
  };

  const handleRunMatching = async () => {
    setMatching(true);
    setActionMessage(null);
    try {
      const res = await evaluateMatching(projectId);
      setActionMessage(
        `Matching complete: ${res.auto_linked_count} auto-linked to CPM activities, ${res.review_queued_count} queued for human review.`
      );
      onScheduleUpdated();
      await refreshAll();
    } catch (err: any) {
      setActionMessage(`Matching failed: ${err.message}`);
    } finally {
      setMatching(false);
    }
  };

  const handleDecision = async (
    eventId: string,
    decision: "APPROVED" | "REJECTED" | "REASSIGNED"
  ) => {
    setSubmittingReview((prev) => ({ ...prev, [eventId]: true }));
    try {
      const actId = selectedCandidates[eventId] || undefined;
      const adjStr = adjustPercents[eventId];
      const adjVal = adjStr ? parseFloat(adjStr) : undefined;
      const notes = reviewNotes[eventId];

      await submitReviewDecision({
        event_id: eventId,
        decision,
        activity_id: actId,
        adjustment_percent: adjVal,
        reviewer_id: "lead-planner",
        notes,
      });

      onScheduleUpdated();
      await refreshAll();
    } catch (err: any) {
      alert(`Review decision failed: ${err.message}`);
    } finally {
      setSubmittingReview((prev) => ({ ...prev, [eventId]: false }));
    }
  };

  // Pipeline counts
  const uploadedCount = artifacts.length;
  const extractedCount = artifacts.filter(
    (a) => a.extraction_status === "EXTRACTED" || a.extraction_status === "PROCESSED" || a.extraction_status === "COMPLETED"
  ).length;
  const reviewCount = queueItems.length;
  const appliedCount = auditLogs.filter(
    (a) => a.action === "PROGRESS_APPLIED" || a.action === "SCHEDULE_UPDATE" || a.action?.includes("APPLY")
  ).length;
  const matchedCount = Math.max(0, extractedCount - reviewCount);

  const scrollToReview = () => {
    reviewQueueRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="space-y-6">
      {/* 1. Execution Pipeline Status Strip */}
      <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-2xs">
        <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500 mb-3">
          Field Execution Pipeline
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
            <div className="text-[10px] font-semibold uppercase text-slate-400">1. Uploaded</div>
            <div className="mt-1 text-xl font-bold font-mono text-slate-900">{uploadedCount}</div>
            <div className="text-[10px] text-slate-500 mt-0.5">Artifacts in MinIO</div>
          </div>

          <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
            <div className="text-[10px] font-semibold uppercase text-slate-400">2. Extracted</div>
            <div className="mt-1 text-xl font-bold font-mono text-blue-600">{extractedCount}</div>
            <div className="text-[10px] text-slate-500 mt-0.5">LLM Event Parsing</div>
          </div>

          <div className="p-3 bg-slate-50 rounded-md border border-slate-100">
            <div className="text-[10px] font-semibold uppercase text-slate-400">3. Matched</div>
            <div className="mt-1 text-xl font-bold font-mono text-indigo-600">{matchedCount}</div>
            <div className="text-[10px] text-slate-500 mt-0.5">Multi-signal linking</div>
          </div>

          <div className="p-3 bg-amber-50/60 rounded-md border border-amber-200">
            <div className="text-[10px] font-semibold uppercase text-amber-700">4. Review Queue</div>
            <div className="mt-1 text-xl font-bold font-mono text-amber-800">{reviewCount}</div>
            <div className="text-[10px] text-amber-600 mt-0.5">Awaiting decision</div>
          </div>

          <div className="p-3 bg-emerald-50/60 rounded-md border border-emerald-200">
            <div className="text-[10px] font-semibold uppercase text-emerald-700">5. Applied</div>
            <div className="mt-1 text-xl font-bold font-mono text-emerald-800">{appliedCount}</div>
            <div className="text-[10px] text-emerald-600 mt-0.5">Committed to CPM</div>
          </div>
        </div>
      </div>

      {/* 2. Priority Attention Required Banner */}
      {reviewCount > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 shadow-2xs">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
            <div>
              <h4 className="text-xs font-bold text-amber-900 uppercase tracking-wider">
                Attention Required: {reviewCount} Ambiguous Field Events
              </h4>
              <p className="text-xs text-amber-800 mt-0.5">
                Automated multi-signal matching flagged candidate conflicts or sub-threshold confidence scores. Review required before updating CPM network.
              </p>
            </div>
          </div>
          <button
            onClick={scrollToReview}
            className="rounded-md bg-amber-600 hover:bg-amber-700 text-white px-3.5 py-1.5 text-xs font-semibold shadow-2xs transition-colors shrink-0 self-start sm:self-auto"
          >
            Review Now ↓
          </button>
        </div>
      )}

      {/* Action Messages */}
      {actionMessage && (
        <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-blue-600 shrink-0" />
            <span>{actionMessage}</span>
          </div>
          <button onClick={() => setActionMessage(null)} className="text-blue-500 hover:text-blue-700">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* 3. Ingestion & Pipeline Actions Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Upload Dropzone */}
        <div className="lg:col-span-2 rounded-lg border border-slate-200 bg-white p-5 shadow-2xs space-y-4">
          <div className="border-b border-slate-100 pb-3">
            <h3 className="text-sm font-bold uppercase tracking-wider text-slate-900">
              Upload Field Report &amp; Evidence
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Ingest PDF daily logs, XLSX progress sheets, CSV registers, or site supervisor audio
            </p>
          </div>

          <form onSubmit={handleUpload} className="space-y-4">
            {/* Drag & Drop Target */}
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragOver(true);
              }}
              onDragLeave={() => setIsDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragOver(false);
                if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                  setSelectedFile(e.dataTransfer.files[0]);
                }
              }}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
                isDragOver
                  ? "border-blue-500 bg-blue-50/50"
                  : selectedFile
                  ? "border-emerald-400 bg-emerald-50/30"
                  : "border-slate-300 hover:border-blue-400 hover:bg-slate-50/50"
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept=".pdf,.xlsx,.csv,.xls,.mp3,.wav,.m4a,.webm"
                onChange={(e) => {
                  if (e.target.files && e.target.files[0]) {
                    setSelectedFile(e.target.files[0]);
                  }
                }}
              />

              <div className="flex flex-col items-center gap-2">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-blue-50 text-blue-600">
                  <UploadCloud className="h-5 w-5" />
                </div>
                {selectedFile ? (
                  <div>
                    <div className="text-xs font-bold text-slate-900 font-mono">
                      {selectedFile.name}
                    </div>
                    <div className="text-[11px] text-slate-500">
                      {(selectedFile.size / 1024).toFixed(1)} KB • Ready to upload
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="text-xs font-bold text-slate-800">
                      Drag &amp; drop field report, or click to browse
                    </div>
                    <div className="text-[11px] text-slate-400 mt-0.5">
                      Supports PDF, Excel (.xlsx/.xls), CSV, and audio recordings
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Optional Report ID & Auto-Process */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
              <div className="flex items-center gap-2 flex-1 max-w-xs">
                <span className="text-slate-500 shrink-0">Report ID:</span>
                <input
                  type="text"
                  placeholder="Optional (e.g. DSR-2026-09-23)"
                  value={reportId}
                  onChange={(e) => setReportId(e.target.value)}
                  className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-900 placeholder:text-slate-400 w-full font-mono"
                />
              </div>

              <label className="flex items-center gap-2 text-slate-700 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={autoProcess}
                  onChange={(e) => setAutoProcess(e.target.checked)}
                  className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                <span>Auto-extract &amp; match immediately</span>
              </label>
            </div>

            <div className="flex items-center justify-between pt-2">
              <button
                type="submit"
                disabled={!selectedFile || uploading}
                className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-2xs hover:bg-blue-500 disabled:opacity-50 transition-colors"
              >
                <UploadCloud className="h-4 w-4" />
                <span>{uploading ? "Ingesting..." : "Upload & Ingest to MinIO"}</span>
              </button>

              {uploadSuccessMsg && (
                <span className="text-xs font-semibold text-emerald-600 flex items-center gap-1.5">
                  <Check className="h-4 w-4" /> {uploadSuccessMsg}
                </span>
              )}
            </div>
          </form>
        </div>

        {/* Pipeline Controls & Export */}
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-2xs flex flex-col justify-between space-y-4">
          <div>
            <div className="flex items-center gap-2 text-slate-900 font-bold text-sm uppercase tracking-wider">
              <Sliders className="h-4 w-4 text-indigo-600" />
              <span>Pipeline Operations</span>
            </div>
            <p className="mt-1 text-xs text-slate-500 leading-relaxed">
              Trigger asynchronous LLM extraction, evaluate multi-signal CPM matching, and export updated schedules.
            </p>
          </div>

          <div className="space-y-2.5">
            {activeArtifactId && (
              <button
                onClick={() => handleRunExtraction(activeArtifactId, true)}
                disabled={extracting}
                className="w-full rounded-md bg-indigo-50 border border-indigo-200 px-3 py-2 text-xs font-bold text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 flex items-center justify-center gap-2"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${extracting ? "animate-spin" : ""}`} />
                <span>Re-extract Active Artifact</span>
              </button>
            )}

            <button
              onClick={handleRunMatching}
              disabled={matching}
              className="w-full rounded-md bg-slate-900 px-3 py-2 text-xs font-bold text-white hover:bg-slate-800 disabled:opacity-50 flex items-center justify-center gap-2 shadow-2xs"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${matching ? "animate-spin" : ""}`} />
              <span>Run Matching &amp; Auto-Link</span>
            </button>

            <a
              href={getExportXerUrl(projectId)}
              download
              className="w-full rounded-md bg-emerald-50 border border-emerald-200 px-3 py-2 text-xs font-bold text-emerald-800 hover:bg-emerald-100 flex items-center justify-center gap-2"
            >
              <Download className="h-3.5 w-3.5" />
              <span>Export Updated P6 (.XER)</span>
            </a>
          </div>
        </div>
      </div>

      {/* 4. Human Review Queue Section */}
      <div ref={reviewQueueRef} className="space-y-4 pt-2">
        <div className="flex items-center justify-between border-b border-slate-200/80 pb-3">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-bold text-slate-900">
              Planner Verification Queue
            </h3>
            <span className="rounded-full bg-amber-100 border border-amber-200 text-amber-800 font-mono text-xs px-2.5 py-0.5 font-bold">
              {queueItems.length} Ambiguous
            </span>
          </div>

          <button
            onClick={refreshAll}
            className="text-xs font-semibold text-slate-500 hover:text-slate-800 flex items-center gap-1"
          >
            <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin text-blue-600" : ""}`} />
            <span>Refresh Queue</span>
          </button>
        </div>

        {queueItems.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="Review Queue Clear"
            description="No pending ambiguous events. All verified field progress has been processed or auto-linked to CPM activities."
          />
        ) : (
          <div className="space-y-4">
            {queueItems.map((item) => {
              const ev = item.event;
              const isSubmitting = submittingReview[ev.event_id] || false;
              const topCand = item.candidates[0];

              return (
                <div
                  key={ev.event_id}
                  className="rounded-lg border border-amber-200 bg-amber-50/20 p-5 space-y-4 shadow-2xs"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-b border-amber-100 pb-3">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-bold text-amber-900 bg-amber-100 px-2 py-0.5 rounded border border-amber-200">
                        {ev.source_document_name}
                      </span>
                      <span className="text-xs text-slate-500 font-mono">
                        Date: {ev.execution_date} | Page {ev.page_number}
                      </span>
                    </div>

                    {item.artifact_view_url && (
                      <a
                        href={item.artifact_view_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-xs font-bold text-blue-600 hover:text-blue-800 underline"
                      >
                        <ExternalLink className="h-3 w-3" /> View Original Evidence in MinIO
                      </a>
                    )}
                  </div>

                  {/* Verbatim Excerpt */}
                  <div className="rounded-md bg-white p-3 border border-slate-200 text-xs">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                      Verbatim Field Report Excerpt
                    </div>
                    <div className="font-mono text-slate-800 leading-relaxed">
                      {ev.verbatim_excerpt}
                    </div>
                    {ev.quantity && (
                      <div className="mt-1.5 text-slate-500 text-[11px] font-mono">
                        Reported Quantity:{" "}
                        <strong className="text-slate-800">
                          {ev.quantity} {ev.unit}
                        </strong>{" "}
                        | Location: <strong className="text-slate-800">{ev.location || "N/A"}</strong>
                      </div>
                    )}
                  </div>

                  {/* Candidate Selection */}
                  <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">
                      Candidate Activities &amp; Match Confidence Scores:
                    </label>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {item.candidates.map((cand) => {
                        const isSelected =
                          (selectedCandidates[ev.event_id] || topCand?.activity_id) === cand.activity_id;

                        return (
                          <div
                            key={cand.activity_id}
                            onClick={() =>
                              setSelectedCandidates((prev) => ({
                                ...prev,
                                [ev.event_id]: cand.activity_id,
                              }))
                            }
                            className={`p-3 rounded-md border text-xs cursor-pointer transition-all ${
                              isSelected
                                ? "border-blue-500 bg-blue-50/50 shadow-2xs"
                                : "border-slate-200 bg-white hover:bg-slate-50"
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-mono font-bold text-slate-900">
                                {cand.activity_code}
                              </span>
                              <span
                                className={`font-mono text-[11px] font-bold px-1.5 py-0.5 rounded ${
                                  cand.match_score >= 0.8
                                    ? "bg-emerald-100 text-emerald-800"
                                    : "bg-amber-100 text-amber-800"
                                }`}
                              >
                                {Math.round(cand.match_score * 100)}% Match
                              </span>
                            </div>
                            <div className="font-medium text-slate-700 mt-1 truncate">
                              {cand.activity_name}
                            </div>
                            <div className="text-[10px] text-slate-400 mt-0.5">
                              Signal Breakdown: Text {cand.score_breakdown?.semantic_text || "-"} | WBS{" "}
                              {cand.score_breakdown?.wbs_context || "-"} | Discipline{" "}
                              {cand.score_breakdown?.discipline_match || "-"}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Decision Controls: Approve, Reassign, Reject */}
                  <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-amber-100">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-500">Progress Override:</span>
                      <input
                        type="number"
                        min="0"
                        max="100"
                        placeholder="%"
                        value={adjustPercents[ev.event_id] || ""}
                        onChange={(e) =>
                          setAdjustPercents((prev) => ({
                            ...prev,
                            [ev.event_id]: e.target.value,
                          }))
                        }
                        className="w-16 rounded border border-slate-300 bg-white px-2 py-1 text-xs font-mono"
                      />
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleDecision(ev.event_id, "APPROVED")}
                        disabled={isSubmitting}
                        className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white shadow-2xs disabled:opacity-50 transition-colors"
                      >
                        <Check className="h-3.5 w-3.5" />
                        <span>Approve &amp; Apply</span>
                      </button>

                      <button
                        onClick={() => handleDecision(ev.event_id, "REASSIGNED")}
                        disabled={isSubmitting}
                        className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 hover:bg-blue-500 px-3 py-1.5 text-xs font-semibold text-white shadow-2xs disabled:opacity-50 transition-colors"
                      >
                        <span>Reassign</span>
                      </button>

                      <button
                        onClick={() => handleDecision(ev.event_id, "REJECTED")}
                        disabled={isSubmitting}
                        className="inline-flex items-center gap-1.5 rounded-md bg-slate-200 hover:bg-rose-100 hover:text-rose-700 px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-2xs disabled:opacity-50 transition-colors"
                      >
                        <X className="h-3.5 w-3.5" />
                        <span>Reject</span>
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 5. Ingested Artifacts History */}
      <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-2xs space-y-3">
        <h4 className="text-xs font-bold uppercase tracking-wider text-slate-900">
          Stored Evidence Artifacts (MinIO S3)
        </h4>

        {artifacts.length === 0 ? (
          <p className="text-xs text-slate-400 py-4 text-center">
            No artifacts ingested for this project.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs text-slate-600">
              <thead className="bg-slate-50 text-[11px] uppercase font-semibold text-slate-400 border-b border-slate-200">
                <tr>
                  <th className="px-3 py-2">Filename</th>
                  <th className="px-3 py-2">SHA-256 Digest</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Uploaded</th>
                  <th className="px-3 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 font-mono text-[11px]">
                {artifacts.map((art) => (
                  <tr key={art.artifact_id} className="hover:bg-slate-50/50">
                    <td className="px-3 py-2 font-bold text-slate-900 font-sans">
                      {art.original_filename}
                    </td>
                    <td className="px-3 py-2 text-slate-400 truncate max-w-xs">
                      {art.sha256}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-col gap-0.5">
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                            art.extraction_status === "EXTRACTED"
                              ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                              : art.extraction_status === "NEEDS_REVIEW"
                              ? "bg-amber-50 text-amber-700 border border-amber-200"
                              : art.extraction_status === "FAILED"
                              ? "bg-rose-50 text-rose-700 border border-rose-200"
                              : art.extraction_status === "PROCESSING"
                              ? "bg-blue-50 text-blue-700 border border-blue-200 animate-pulse"
                              : "bg-slate-100 text-slate-700 border border-slate-200"
                          }`}
                        >
                          {art.extraction_status}
                        </span>
                        {art.error_message && (
                          <span className="text-[10px] text-slate-500 max-w-xs truncate" title={art.error_message}>
                            {art.error_message}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-slate-500">
                      {art.uploaded_at ? new Date(art.uploaded_at).toLocaleDateString() : "-"}
                    </td>
                    <td className="px-3 py-2 text-right font-sans">
                      <button
                        onClick={() => handleRunExtraction(art.artifact_id, true)}
                        className={`text-xs font-semibold ${
                          art.extraction_status === "FAILED" || art.extraction_status === "NEEDS_REVIEW"
                            ? "text-amber-600 hover:text-amber-800"
                            : "text-blue-600 hover:text-blue-800"
                        }`}
                        title="Re-run extraction engine on stored artifact"
                      >
                        {art.extraction_status === "FAILED" || art.extraction_status === "NEEDS_REVIEW" ? "Retry" : "Re-extract"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
