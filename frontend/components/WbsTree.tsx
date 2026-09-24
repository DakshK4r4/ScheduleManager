"use client";

import React, { useState, useEffect, useMemo } from "react";
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FolderOpen,
  Layers,
  RefreshCw,
  FolderTree,
  Activity as ActivityIcon,
  ShieldAlert,
  Clock,
  ChevronsDown,
  ChevronsUp,
} from "lucide-react";
import { fetchProjectWbsTree, fetchActivities, fetchProjectCPM } from "@/lib/api";
import { WBSTreeNode, Activity, CPMResult } from "@/lib/types";

interface WbsTreeProps {
  projectId: string;
  onSelectWbs?: (wbsId: string) => void;
}

interface WbsNodeMetrics {
  totalActivities: number;
  avgCompletion: number;
  criticalCount: number;
  delayedCount: number;
}

interface TreeNodeItemProps {
  node: WBSTreeNode;
  level?: number;
  isLast?: boolean;
  expandedMap: Record<string, boolean>;
  onToggleExpand: (id: string) => void;
  onSelectWbs?: (wbsId: string) => void;
  metricsMap: Record<string, WbsNodeMetrics>;
}

function TreeNodeItem({
  node,
  level = 0,
  isLast = false,
  expandedMap,
  onToggleExpand,
  onSelectWbs,
  metricsMap,
}: TreeNodeItemProps) {
  const hasChildren = node.children && node.children.length > 0;
  const isExpanded = expandedMap[node.id] !== false; // default open
  const metrics = metricsMap[node.id] || {
    totalActivities: node.activity_count || 0,
    avgCompletion: 0,
    criticalCount: 0,
    delayedCount: 0,
  };

  return (
    <div className="select-none">
      <div
        style={{ paddingLeft: `${level * 24 + 12}px` }}
        className="flex flex-col sm:flex-row sm:items-center sm:justify-between py-2.5 pr-4 hover:bg-blue-50/60 rounded-md cursor-pointer transition-colors group gap-2"
        onClick={() => {
          if (hasChildren) onToggleExpand(node.id);
          if (onSelectWbs) onSelectWbs(node.id);
        }}
      >
        {/* Left: Code, Icon, Name */}
        <div className="flex items-center gap-2 min-w-0">
          {hasChildren ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onToggleExpand(node.id);
              }}
              className="p-1 text-slate-400 hover:text-slate-700 rounded transition-colors"
            >
              {isExpanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </button>
          ) : (
            <span className="w-5" />
          )}

          {hasChildren ? (
            isExpanded ? (
              <FolderOpen className="h-4 w-4 text-blue-600 shrink-0" />
            ) : (
              <Folder className="h-4 w-4 text-blue-500 shrink-0" />
            )
          ) : (
            <Layers className="h-4 w-4 text-slate-400 shrink-0" />
          )}

          <span className="font-mono text-xs font-bold text-slate-900 bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200">
            {node.code}
          </span>
          <span className="text-xs font-semibold text-slate-800 truncate">{node.name}</span>
        </div>

        {/* Right: Metrics Strip (Activities, Completion, Critical, Delayed) */}
        <div className="flex items-center gap-3 text-xs pl-7 sm:pl-0 shrink-0">
          {/* Progress Bar & % */}
          <div className="flex items-center gap-2">
            <div className="h-1.5 w-16 rounded-full bg-slate-100 overflow-hidden">
              <div
                className="h-full bg-blue-600 rounded-full"
                style={{ width: `${metrics.avgCompletion}%` }}
              />
            </div>
            <span className="font-mono text-[11px] text-slate-600 w-8 text-right">
              {metrics.avgCompletion}%
            </span>
          </div>

          {/* Critical Count */}
          {metrics.criticalCount > 0 && (
            <span
              title={`${metrics.criticalCount} critical path activities in package`}
              className="inline-flex items-center gap-1 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-700 border border-rose-200"
            >
              <ShieldAlert className="h-3 w-3" />
              <span>{metrics.criticalCount} crit</span>
            </span>
          )}

          {/* Delayed Count */}
          {metrics.delayedCount > 0 && (
            <span
              title={`${metrics.delayedCount} delayed activities in package`}
              className="inline-flex items-center gap-1 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-bold text-amber-700 border border-amber-200"
            >
              <Clock className="h-3 w-3" />
              <span>{metrics.delayedCount} late</span>
            </span>
          )}

          {/* Activities Count Badge */}
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 border border-slate-200 font-mono">
            {metrics.totalActivities} {metrics.totalActivities === 1 ? "act" : "acts"}
          </span>
        </div>
      </div>

      {/* Children with vertical guide line */}
      {hasChildren && isExpanded && (
        <div className="relative border-l border-slate-200 ml-6 pl-2 space-y-0.5">
          {node.children.map((child, idx) => (
            <TreeNodeItem
              key={child.id}
              node={child}
              level={level + 1}
              isLast={idx === node.children.length - 1}
              expandedMap={expandedMap}
              onToggleExpand={onToggleExpand}
              onSelectWbs={onSelectWbs}
              metricsMap={metricsMap}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function WbsTree({ projectId, onSelectWbs }: WbsTreeProps) {
  const [treeData, setTreeData] = useState<WBSTreeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedMap, setExpandedMap] = useState<Record<string, boolean>>({});
  const [metricsMap, setMetricsMap] = useState<Record<string, WbsNodeMetrics>>({});

  const loadData = async () => {
    setLoading(true);
    try {
      const [tree, actsRes, cpm] = await Promise.all([
        fetchProjectWbsTree(projectId),
        fetchActivities(projectId, { page_size: 1000 }),
        fetchProjectCPM(projectId).catch(() => null),
      ]);

      setTreeData(tree);

      // Compute metrics per WBS
      const wbsActs: Record<string, Activity[]> = {};
      actsRes.items.forEach((a) => {
        if (a.wbs_id) {
          if (!wbsActs[a.wbs_id]) wbsActs[a.wbs_id] = [];
          wbsActs[a.wbs_id].push(a);
        }
      });

      const critSet = new Set(cpm?.critical_activities || []);
      const now = new Date();

      // Recursive helper to accumulate activities for a node and all its children
      const getDescendantActivities = (node: WBSTreeNode): Activity[] => {
        let acts = [...(wbsActs[node.id] || [])];
        if (node.children) {
          for (const ch of node.children) {
            acts = acts.concat(getDescendantActivities(ch));
          }
        }
        return acts;
      };

      const m: Record<string, WbsNodeMetrics> = {};
      const calculateNodeMetrics = (node: WBSTreeNode) => {
        const nodeActs = getDescendantActivities(node);
        const total = nodeActs.length;
        let sumPct = 0;
        let critCount = 0;
        let delayCount = 0;

        nodeActs.forEach((a) => {
          sumPct += a.percent_complete || 0;
          if (critSet.has(a.activity_code)) critCount++;
          if (a.status !== "COMPLETED" && a.planned_finish && new Date(a.planned_finish) < now) {
            delayCount++;
          }
        });

        m[node.id] = {
          totalActivities: total,
          avgCompletion: total > 0 ? Math.round(sumPct / total) : 0,
          criticalCount: critCount,
          delayedCount: delayCount,
        };

        if (node.children) {
          node.children.forEach(calculateNodeMetrics);
        }
      };

      tree.forEach(calculateNodeMetrics);
      setMetricsMap(m);

      // Initialize all expanded by default
      const initialExp: Record<string, boolean> = {};
      const markExpanded = (n: WBSTreeNode) => {
        initialExp[n.id] = true;
        if (n.children) n.children.forEach(markExpanded);
      };
      tree.forEach(markExpanded);
      setExpandedMap(initialExp);
    } catch (err) {
      console.error("Failed to load WBS hierarchy:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [projectId]);

  const handleToggleExpand = (id: string) => {
    setExpandedMap((prev) => ({
      ...prev,
      [id]: prev[id] === false ? true : false,
    }));
  };

  const handleExpandAll = () => {
    const updated: Record<string, boolean> = {};
    const expandRecursive = (nodes: WBSTreeNode[]) => {
      nodes.forEach((n) => {
        updated[n.id] = true;
        if (n.children) expandRecursive(n.children);
      });
    };
    expandRecursive(treeData);
    setExpandedMap(updated);
  };

  const handleCollapseAll = () => {
    const updated: Record<string, boolean> = {};
    const collapseRecursive = (nodes: WBSTreeNode[]) => {
      nodes.forEach((n) => {
        updated[n.id] = false;
        if (n.children) collapseRecursive(n.children);
      });
    };
    collapseRecursive(treeData);
    setExpandedMap(updated);
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-2xs space-y-4">
      {/* Header controls & summary */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between border-b border-slate-100 pb-3.5 gap-3">
        <div>
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-900">
            Work Breakdown Structure (WBS)
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Hierarchical packages with roll-up completion %, critical activity density, and schedule delays
          </p>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto">
          <button
            onClick={handleExpandAll}
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 transition-colors"
          >
            <ChevronsDown className="h-3.5 w-3.5 text-slate-500" />
            <span>Expand All</span>
          </button>

          <button
            onClick={handleCollapseAll}
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 transition-colors"
          >
            <ChevronsUp className="h-3.5 w-3.5 text-slate-500" />
            <span>Collapse All</span>
          </button>

          <button
            onClick={loadData}
            className="rounded-md border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 transition-colors"
            title="Refresh WBS Hierarchy"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin text-blue-600" : ""}`} />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-xs text-slate-400">Loading WBS hierarchy...</div>
      ) : treeData.length === 0 ? (
        <div className="py-12 text-center text-xs text-slate-400">
          No WBS hierarchy defined for this project.
        </div>
      ) : (
        <div className="space-y-1">
          {treeData.map((node, idx) => (
            <TreeNodeItem
              key={node.id}
              node={node}
              isLast={idx === treeData.length - 1}
              expandedMap={expandedMap}
              onToggleExpand={handleToggleExpand}
              onSelectWbs={onSelectWbs}
              metricsMap={metricsMap}
            />
          ))}
        </div>
      )}
    </div>
  );
}
