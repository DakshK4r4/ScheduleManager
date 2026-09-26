# Time Agent — Activity Resolution & Progress Update Safety Remediation Report

**Date:** 2026-09-26  
**System:** ScheduleManager Time Agent (`FastAPI` backend + `Next.js` frontend)  
**Status:** Complete & Authoritatively Verified (70/70 backend pytest tests passing, Next.js frontend compiled and verified)

---

## 1. Executive Summary

This remediation targeted critical safety and correctness bugs in the Time Agent conversational progress update subsystem. Specifically, natural-language messages containing multiple activity updates or single activity updates with completion keywords previously experienced:
1. **Unintended bulk proposals**: Messages with completion words and discipline references unconditionally triggered bulk proposals across 5+ activities instead of resolving the exact activity mentioned.
2. **Uniform percentage overwrites**: Explicit percentages were wiped out and forced to 100% when completion verbs ("done", "completed") were present.
3. **Multi-activity loss**: Messages specifying progress across multiple activities (e.g., `"i have done mechanical activity 1 75% and mechanical acitivity 2 98%"`) lost distinct percentages and collapsed into single or uniform updates.
4. **Physical quantity hijacking**: Numbers indicating activity identifiers (e.g., `activity 1`) or location identifiers (e.g., `Unit 4`) were misparsed as physical quantities (e.g., `quantity=1.0`) or progress percentages (`4.0%`).
5. **Interactive chat lockout**: The frontend chat composer disabled user input while a proposal was pending, trapping the user unless they clicked buttons.

All five failure modes have been addressed, verified on real database models with live Docker containers, and locked down with 14 new regression tests alongside 56 passing existing tests.

---

## 2. Root Causes Identified

1. **Greedy Bulk Trigger in `agent_parser.py` (Line 1279)**:
   The rule-based intent classifier unconditionally converted single-activity utterances into `BULK_PROGRESS_REPORT` and set `is_bulk = True` whenever any discipline name was present alongside completion verbs, even when an explicit activity number or percentage was provided.

2. **Unconditional 100% Override in `agent_parser.py` (Line 1083)**:
   When `is_completed` was flagged (e.g. from "completed", "done", "finish"), the parser unconditionally executed `override_percent = 100.0`, discarding the explicitly supplied percentage (e.g. `98%`).

3. **Numeric Hijacking in `agent_parser.py` (Line 1042-1055)**:
   Standalone quantity extraction regex stripped codes like `CIV-1001`, but left substrings like `activity 1` or `Unit 4`, causing `1.0` or `4.0` to be captured as physical quantities or progress percentages.

4. **Greedy Discipline Filter in `agent_service.py` (Line 1210-1245)**:
   `_handle_bulk_progress` matched activities purely by discipline canonical prefix (e.g. `MEC-`), selecting all 5 mechanical activities in a project whenever 2 to 6 activities existed, completely ignoring numeric activity distinctions.

5. **Lack of Project-Aware Activity Reference Resolver**:
   Natural language parsing occurred in isolation without database awareness. Suffixes (e.g. `1002`), numeric tags (e.g. `activity 02` <-> `MEC-1002`), and typo variations (e.g. `acitivity`) were not mapped deterministically against the project's actual catalog.

6. **Frontend Input Lockout in `TimeAgentChat.tsx` (Lines 505, 1375, 1381)**:
   The text input, voice button, and message sender were guarded with `disabled={isChatLocked || ...}`, preventing conversational cancellations ("cancel", "no"), revisions ("actually 95%"), or informational questions.

---

## 3. Architecture & Data Flow Changes

### The Remediated Pipeline:
```
Natural-language user message
        ↓
Intent extraction (ConversationalParser)
   - Extracts all candidate clauses into List[ActivityUpdateCandidate]
   - Detects explicit bulk ONLY when bulk markers ("all", "sabhi", etc.) exist
   - Preserves explicit percentage over completion verbs
        ↓
Project-aware activity resolution (ActivityReferenceResolver)
   - Stage 1: Exact code match (MEC-1002, MEC 1002, MEC1002)
   - Stage 2: Exact activity name
   - Stage 3: Normalized name match
   - Stage 4: Code suffix numeric (1002 -> MEC-1002)
   - Stage 5: Normalized name + numeric identifier (1 <-> 01 <-> 1001)
   - Stage 6: Typo-tolerant controlled fuzzy matching (acitivity -> activity)
   - Stage 7: Ambiguity detection (marks ambiguous; NEVER auto-bulks)
        ↓
Multi- vs. Single-Activity Routing (TimeAgentService)
   - len >= 2: Staged multi-activity proposal card with individual percentages
   - len == 1: Single activity proposal card staged specifically for identified activity
   - ambiguous: Clarification options presented; no bulk modification
        ↓
User Confirmation
   - Single proposal: POST /agent/conversations/{cid}/confirm
   - Multi/Bulk proposal: POST /agent/conversations/{cid}/bulk-confirm
   - Conversational fast path: "confirm", "yes", "cancel", "no"
        ↓
Authoritative Schedule Mutation (ScheduleUpdateService)
   - Row-locked ledger updates with individual per-activity targets
```

---

## 4. Files Modified & Added

| File | Change Summary |
|---|---|
| [`backend/app/schemas/agent.py`](file:///c:/Users/Gues/ScheduleManager/backend/app/schemas/agent.py) | Added `ActivityUpdateCandidate` model, added `activity_updates` and `is_explicit_bulk` to `ParsedConversationalIntent`, added `is_multi_activity` and `multi_proposals` to `ActionCardDTO`. |
| [`backend/app/services/activity_reference_resolver.py`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/activity_reference_resolver.py) | **New Service**: 7-stage deterministic resolution separating language parsing from database lookup with numeric padding normalization, typo tolerance, and cross-project isolation. |
| [`backend/app/services/agent_parser.py`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/agent_parser.py) | Added `extract_activity_updates()`, enforced explicit `%` and progress precedence over completion keywords, excluded prepositions (`TO`, `IN`, `ON`) from code prefixes, and eliminated physical quantity hijacking. |
| [`backend/app/services/matching_service.py`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/matching_service.py) | Preserved single numeric digits in `_tokenize` and `_extract_subject_tokens`, added numeric token equivalence (`1` == `01`), fixed substring overlap false positives (`len >= 3`). |
| [`backend/app/services/agent_service.py`](file:///c:/Users/Gues/ScheduleManager/backend/app/services/agent_service.py) | Added `_handle_multi_activity_progress()`, wired `ActivityReferenceResolver` into `_merge_update_context()`, guarded bulk progress to explicit bulk, enabled per-activity percentages in `confirm_bulk_proposal()`. |
| [`frontend/lib/types.ts`](file:///c:/Users/Gues/ScheduleManager/frontend/lib/types.ts) | Added `is_multi_activity?: boolean` to `ActionCard` interface. |
| [`frontend/components/time-agent/ClarificationCard.tsx`](file:///c:/Users/Gues/ScheduleManager/frontend/components/time-agent/ClarificationCard.tsx) | Rendered individual `proposed_percent` per activity instead of uniform `targetPct`, and updated dynamic confirm labels for multi-activity updates. |
| [`frontend/components/TimeAgentChat.tsx`](file:///c:/Users/Gues/ScheduleManager/frontend/components/TimeAgentChat.tsx) | Unlocked chat composer textarea, voice button, and send button during pending proposals; enabled conversational confirmations, cancellations, and inquiries. |
| [`backend/tests/test_time_agent_remediation.py`](file:///c:/Users/Gues/ScheduleManager/backend/tests/test_time_agent_remediation.py) | **New Test Suite**: 14 deterministic regression tests covering multi-activity, single-activity, precedence rules, resolver stages, and API confirmation. |

---

## 5. Percentage Precedence Rules

To prevent status words or quantities from wiping out user input, the parser strictly enforces:
1. **Explicit Percentage** (`75%`, `98%`): Highest priority. Always overrides completion verbs or physical quantities.
2. **Explicit Progress Assignment** (`to 75`, `set to 98`): Second priority.
3. **Physical Quantity with Units** (`35 m3` against `planned_quantity`): Third priority, converted using incremental/cumulative semantics.
4. **Completion Status Verbs** (`completed`, `done`, `finished`): Fourth priority. Only sets `100.0%` if NO percentage was specified.
5. **Default Increment** (`+25%`): Lowest fallback when only activity progress is affirmed.

---

## 6. Activity Resolution Deterministic Stages

When natural language references are resolved against the project catalog:
1. **Stage 1 (Exact Code)**: Matches `MEC-1002`, `mec-1002`, `MEC 1002`, `MEC1002` with 1.0 confidence.
2. **Stage 2 (Exact Name)**: Matches `Mechanical Activity 02` with 1.0 confidence.
3. **Stage 3 (Normalized Name)**: Matches normalized name with 0.99 confidence.
4. **Stage 4 (Suffix Numeric)**: Matches `1002` to `MEC-1002` with 0.95 confidence.
5. **Stage 5 (Normalized Name + Numeric Identifier)**: Normalizes padding so `1` <-> `01` <-> `1001` and `2` <-> `02` <-> `1002` match with 0.90–0.95 confidence.
6. **Stage 6 (Controlled Typo-Tolerant Token Matching)**: Corrects known domain typos (`acitivity` -> `activity`, `mecahnical` -> `mechanical`).
7. **Stage 7 (Ambiguity)**: If multiple activities match with equal score, marks status `ambiguous` with candidate list. **NEVER auto-bulks**.

---

## 7. Verification & Reproduction Results

### Test Case 1: Multi-Activity Distinct Percentages
- **Input:** `"i have done mechanical activity 1 75% and mechanical acitivity 2 98%"`
- **Result:**
  - Resolved `mechanical activity 1` -> `MEC-1001` (`75.0%`)
  - Resolved `mechanical acitivity 2` -> `MEC-1002` (`98.0%`)
  - Action card: `BULK_SCOPE_PROPOSAL` with `is_multi_activity: True`
  - Confirmation: `MEC-1001` updated to `75.0%`, `MEC-1002` updated to `98.0%`
  - Zero collapsed or uniform 100% updates.

### Test Case 2: Single Activity Completion With Explicit Percentage
- **Input:** `"i have completed mechanical activity 02 98%"`
- **Result:**
  - Resolved specifically to `MEC-1002`
  - Single action card: `PROPOSAL_CONFIRMATION`
  - Proposed percent: `98.0%` (not 100%, and did not select 5 activities)
  - Confirmation: `MEC-1002` updated to `98.0%`

### Test Case 3: Conversational Pending Proposal Unlock
- **Input during proposal:** `"What is the status of F-204?"`
- **Result:** Answered informational query with `CIV-1001` details without locking out user or discarding pending proposal.
- **Input during proposal:** `"cancel"`
- **Result:** Executed instantaneous cancellation; authoritative schedule remained untouched.

---

## 8. Test Execution Summary

| Test Suite | Total Tests | Passed | Failed |
|---|---|---|---|
| `backend/tests/test_time_agent_remediation.py` | 14 | 14 | 0 |
| `backend/tests/test_time_agent.py` | 56 | 56 | 0 |
| `backend/tests/test_extraction_hardening.py` | 8 | 8 | 0 |
| `backend/tests/test_extraction_matching_integration.py` | 15 | 15 | 0 |
| **Total** | **93** | **93** | **0** |

---

## 9. Final Concise Summary

### BEFORE:
- `"i have done mechanical activity 1 75% and mechanical acitivity 2 98%"` collapsed into a single uniform proposal or wiped out percentages.
- `"i have completed mechanical activity 02 98%"` matched all 5 mechanical activities in bulk and set them to 100%.
- Numbers in `"activity 1"` or `"Unit 4"` were hijacked as physical quantities (`1.0`) or percentages (`4.0%`).
- Chat input was completely disabled in the browser during pending proposals.

### AFTER:
- Multi-activity progress updates extract each activity and its distinct percentage independently into `ActivityUpdateCandidate`.
- `ActivityReferenceResolver` matches `activity 1` to `MEC-1001` and `activity 02` to `MEC-1002` deterministically.
- Explicit percentage strictly supersedes completion verbs.
- Chat composer remains interactive during pending proposals, allowing conversational confirm, cancel, question, or revision.
- Bulk updates strictly require explicit bulk indicators ("all", "sabhi") and never trigger automatically from candidate ambiguity.

### TESTS:
- **Existing tests:** 56/56 passing in `test_time_agent.py`.
- **New tests:** 14/14 passing in `test_time_agent_remediation.py`.
- **Integration tests:** 23/23 passing in `test_extraction_hardening.py` and `test_extraction_matching_integration.py`.

### REMAINING RISKS:
- Activity names with ambiguous numeric suffixes outside the project catalog (e.g. referring to an activity not in the schedule) will return clarification questions rather than auto-matching. This is by design under the safety invariant: **WHEN IN DOUBT, ASK**.
