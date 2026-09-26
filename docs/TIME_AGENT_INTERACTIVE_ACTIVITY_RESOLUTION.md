# Time Agent — Interactive Activity Resolution & Progressive Disambiguation

**Date:** 2026-09-26  
**System:** ScheduleManager Time Agent (`FastAPI` backend + `Next.js` frontend)  
**Status:** Authoritatively Implemented & Verified (88/88 backend pytest tests passing, live black-box scenarios verified on running Docker stack)

---

## 1. Problem & Objective

Prior to this feature, when a site supervisor entered a natural-language progress update containing an ambiguous activity reference (e.g. `"update the mechanical work to 80%"` where 5 mechanical activities exist in the project), the system risked guessing, converting the request into an unintended bulk update across all 5 activities, or requiring manual cancellation before clarifying.

### Objective:
Implement an Akinator-style progressive disambiguation capability within the Time Agent that:
1. Detects genuinely ambiguous activity references when an update value or progress is reported.
2. Generates a project-scoped candidate set without inventing activities or guessing.
3. Chooses a deterministic distinguishing question maximizing information gain across real metadata (locations, component names, or structured activity choices).
4. Interprets natural answers strictly against the current candidate set (supporting keywords, exact codes, typo tolerance, yes/no responses, and ordinals like `"second one"` against the immediately presented options).
5. Preserves original progress percentages (e.g., `80%`) throughout clarification turns without replacement or overwrite.
6. Enforces a hard bounded limit on clarification rounds (`max_rounds = 3`).
7. Transitions uniquely resolved activities into the standard proposal confirmation flow (`stage_proposal` -> `PROPOSAL_CONFIRMATION` -> user confirm -> authoritative schedule mutation).
8. Strictly preserves the Bulk Safety Invariant: Ambiguity NEVER implies bulk.

---

## 2. Architecture & Data Flow

```
                      User Natural-Language Input
                                 │
                                 ▼
                     ConversationalParser
         - Extracts clauses into ActivityUpdateCandidate
         - Enforces explicit bulk marker requirement (all, sabhi)
         - Preserves explicit % over completion verbs
                                 │
                                 ▼
                   ActivityReferenceResolver
         - Stage 1: Exact code (MEC-1002, MEC1002)
         - Stage 2: Exact activity name
         - Stage 3: Normalized name match
         - Stage 4: Suffix numeric (1002 -> MEC-1002)
         - Stage 5: Normalized name + numeric identifier (1 <-> 01 <-> 1001)
         - Stage 6: Typo-tolerant token matching (acitivity -> activity)
         - Stage 7: Discipline filtering / Ambiguity detection
                                 │
                  ┌──────────────┴──────────────┐
                  │                             │
        Uniquely Resolved                  Ambiguous Match
                  │                             │
                  │                             ▼
                  │                InteractiveActivityResolver
                  │               - Starts ActivityResolutionSession
                  │               - Preserves reported_percent
                  │               - Generates distinguishing question
                  │               - Emits CLARIFICATION_CHOICE card
                  │                             │
                  │                 User Clarification Answer
                  │                             │
                  │                             ▼
                  │                 process_answer(answer, candidates)
                  │               - Ordinals ("second one")
                  │               - Yes/No ("yes", "no")
                  │               - Exact code ("MEC-1004")
                  │               - Typo-tolerant keyword ("pipng")
                  │               - Location ("Unit 4")
                  │                             │
                  └──────────────┬──────────────┘
                                 │
                                 ▼
                   Proposal Staging (TimeAgentService)
                     - Creates persistent UpdateProposal (PENDING)
                     - Emits PROPOSAL_CONFIRMATION action card
                                 │
                                 ▼
                     User Explicit Confirmation
                  - Single: POST /agent/conversations/{cid}/confirm
                  - Multi/Bulk: POST /agent/conversations/{cid}/bulk-confirm
                  - Fast-path: "confirm", "yes", "cancel", "no"
                                 │
                                 ▼
                Authoritative Schedule Mutation (PostgreSQL)
                  - ScheduleUpdateService.apply_event_progress
                  - ActualProgressLedger created
                  - CPM schedule recalculated
```

---

## 3. Session Model & State Machine

The disambiguation state is tracked in `ActivityResolutionSession` and persisted across HTTP requests within `ExecutionEvent.match_metadata` (under `update_context["resolution_session"]`):

```python
class ActivityResolutionSession:
    id: str
    project_id: str
    conversation_id: str
    original_reference: str
    candidate_activity_ids: List[str]
    candidate_options_presented: List[Dict[str, str]]
    extracted_clues: Dict[str, Any]      # Contains reported_percent, quantity, status_reported
    history_log: List[Dict[str, Any]]    # Auditable log of questions, answers, candidate counts
    round_number: int                    # Current clarification turn
    max_rounds: int = 3                  # Hard upper bound
    status: ResolutionSessionStatus      # ACTIVE, RESOLVED, FAILED, CANCELLED
    resolved_activity_id: Optional[str]
```

### State Transitions:
1. `ACTIVE`: Session started with `candidates > 1`. Distinguishing question presented.
2. `RESOLVED`: Answer filtered candidates to `len == 1`. Activity handed to `TimeAgentService.stage_proposal`.
3. `FAILED`: Clarification rounds reached `max_rounds = 3` without unique resolution, or all candidates were eliminated. Handed to Lead Planner Review Queue.
4. `CANCELLED`: User explicitly cancelled via `"cancel"`, `"stop"`, `"radd karo"`. Schedule remains untouched.

---

## 4. Question Selection & Information Gain

The interactive resolver partitions the remaining candidate set using real schedule metadata:
1. **Location Partitioning:**
   If candidates belong to distinct locations (e.g., `Unit 4` vs `Unit 5`), the resolver detects this and asks:
   `"Is this work located in Unit 4 or Unit 5?"`
2. **Component / Name Distinguishing:**
   If candidates share a discipline but have distinct component keywords (e.g., Equipment, Piping, Pump, Testing, Commissioning), the question presents these distinct components:
   `"Which mechanical activity do you mean?"`
3. **Structured Fallback Options:**
   Provides structured choice buttons presenting real activity codes and names, plus a `"None of these"` option.
4. **Multilingual Localization:**
   Questions and options are rendered in English, Hindi (Devanagari), or Hinglish depending on conversation configuration.

---

## 5. Natural-Language Answer Interpretation

Answers are interpreted **strictly against the active candidate set** (preventing project-wide scope jumping):
- **Ordinals:** `"first"`, `"second one"`, `"the 2nd one"`, `"doosra"`, `"number 2"` map to the 1-based index of `candidate_options_presented`.
- **Yes / No:**
  - `"yes"`, `"haan"`, `"sahi hai"` confirms the single activity being queried.
  - `"no"`, `"nahi"`, `"galat hai"` eliminates the queried activity and asks a follow-up.
- **Exact Activity Code:** E.g., `"MEC-1004"` matches immediately against candidate codes.
- **Typo-Tolerant Keywords:** Uses token intersection and Jaccard similarity (e.g. `"pipng"` matches `"Mechanical Piping Installation"`).
- **Locations:** E.g., `"Unit 4"` filters candidate set to those matching `location_code` or location tokens.
- **Cancellation:** `"cancel"`, `"stop"`, `"radd karo"` aborts the session without mutation.
- **None of these:** Eliminates currently presented candidate options.

---

## 6. Safety Invariants Enforced

1. **Ambiguity never mutates schedule:** Schedule updates are never committed while candidates are ambiguous.
2. **Candidate count > 1 never implies bulk:** Multi-candidate matches require interactive disambiguation; they are never treated as bulk updates.
3. **Explicit percentage precedence:** Percentages (e.g., `80%`, `98%`) are never overwritten by completion verbs ("done", "completed").
4. **Activity numbers are not physical quantities:** E.g., `activity 1` is never converted to `quantity=1.0` or `1%`.
5. **Location numbers are not progress:** E.g., `Unit 4` is never parsed as `4%`.
6. **Cross-project candidate isolation:** Candidates are strictly scoped to `project_id`.
7. **No invented activities:** Candidates and option labels originate from authoritative database records.
8. **Bounded clarification rounds:** Clarification strictly terminates after `max_rounds = 3`.
9. **No infinite clarification loop:** Repeated identical answers or unresolvable sets fail gracefully to manual review.
10. **Zero premature mutation:** Schedule database rows are mutated strictly after explicit proposal confirmation.
11. **Multi-activity independence:** Multi-activity messages retain per-activity percentages even if one activity is ambiguous.

---

---

## 6.1 Section 32: NO-MATCH / Non-Existent Activities

When a site supervisor or user refers to an activity that does not exist in the current project's schedule (e.g. `"I completed the mechanical welding activity to 80%."` when the schedule contains piping and equipment but no welding):

1. **Resolution Status:** `resolution_status = NO_MATCH` (`method = "no_match"`).
2. **Deterministic Threshold Contract:**
   - `RESOLUTION_THRESHOLD = 0.65`: Minimum score for an activity to be considered resolved.
   - `SUGGESTION_THRESHOLD = 0.35`: Minimum score for an activity to be presented as a plausible suggestion. Candidates with scores below 0.35 are discarded.
   - `MARGIN_DELTA_THRESHOLD = 0.25`: Minimum gap required between the top candidate and runner-up for single-candidate resolution.
3. **Strict Non-Match Safety Invariants:**
   - **Do NOT** select the closest fuzzy match automatically.
   - **Do NOT** select an activity merely because it belongs to the same discipline.
   - **Do NOT** convert the request into a bulk update.
   - **Do NOT** invent or hallucinate an activity.
   - **Do NOT** create an `UpdateProposal` (`len(pending_proposals) == 0`).
   - **Do NOT** mutate authoritative schedule state (`0%` mutation).
4. **Current Project Schedule Contextualized Wording:**
   - The response must explicitly state that the activity was not found in the **current project schedule**, never claiming the activity does not exist in reality:
     - **English (with suggestions):**
       `"I couldn't find a scheduled activity matching '<reference>' in this project's schedule. These scheduled activities may be related:\n1. ...\n2. ...\n\nThese are possible suggestions, not confirmed matches. Please provide the activity code or a more specific description."`
     - **English (no suggestions):**
       `"I couldn't find a scheduled activity matching that description in this project's schedule. Please provide the activity code or a more specific description."`
     - **Hindi / Hinglish:** Explicitly incorporates `"प्रोजेक्ट के शेड्यूल में"` / `"project ke schedule mein"`.

---

## 7. Frontend Integration

1. **ActionCardDTO (`CLARIFICATION_CHOICE`):**
   - Transmits `resolution_session_id` and `is_resolution_question = true`.
   - Distinct amber card styling with "Activity Identification" badge separating identification questions from mutation confirmation.
2. **Textarea & Voice Composer Unlocked:**
   - site supervisors can click option buttons or freely type natural language clarifications (e.g., `"second one"`, `"piping"`, `"cancel"`).

---

## 8. Test Verification Summary

### Pytest Backend Test Suites:
| Test Suite | Total Tests | Passed | Description |
|---|---|---|---|
| `tests/test_no_match_activity_resolution.py` | 6 | **6 passed** | Section 32 NO_MATCH invariants, non-existent activities, discipline isolation, multilingual wording, threshold contract |
| `tests/test_interactive_activity_resolution.py` | 18 | **18 passed** | Progressive disambiguation, ordinals, yes/no, code answers, typo tolerance, max rounds, audit trail |
| `tests/test_time_agent_remediation.py` | 14 | **14 passed** | ActivityReferenceResolver 7 stages, percentage precedence, multi-activity, bulk safety |
| `tests/test_time_agent.py` | 56 | **56 passed** | Legacy Time Agent tests, multi-turn context accumulation, language locking, fast cancellation |
| **Total** | **94** | **94 passed** | **100% pass rate** |

### Live Black-Box Verification on Docker Stack (`scripts/verify_live_scenarios.py`):
- **Scenario 1:** `"update the mechanical work to 80%"` -> returned `CLARIFICATION_CHOICE` with 7 mechanical options. 0% schedule mutation.
- **Scenario 2:** Disambiguation answer `"second one"` -> resolved to `MEC-1002` at 80.0%, staged `PROPOSAL_CONFIRMATION`. Confirmed and mutated `MEC-1002` to 80.0%.
- **Scenario 3:** `"i have completed mechanical activity 02 98%"` -> resolved specifically to `MEC-1002` at 98.0% (precedence preserved over "completed").
- **Scenario 4:** `"i have done mechanical activity 1 75% and mechanical activity 2 98%"` -> staged multi-activity proposal `MEC-1001` (75%) and `MEC-1002` (98%). Confirmed and applied accurately.
- **Scenario 5:** `"update all mechanical activities to 80%"` -> explicit bulk intent correctly routed to standard bulk proposal with `target_percent=80.0%`.
- **Scenario 6 (Section 32):** `"I completed the mechanical welding activity to 80%."` -> returns NO_MATCH statement referencing current project schedule with suggestions, 0 proposals, 0 mutations.

---

## 9. Known Limitations

1. **Database Schema:** `ExecutionEvent.match_metadata` is utilized for persistence to avoid altering table schemas. In a future migration, a dedicated `resolution_sessions` table could be added if standalone indexing across conversations is desired.
2. **Complex Boolean Disjunctions:** Clarification answers with mixed conjunctions (e.g., `"neither the first nor the third but the second"`) resolve the positive component (`"second"`) but do not evaluate nested boolean logic trees.
