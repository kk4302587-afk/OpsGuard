# Implement Topology-Based RCA View

## Background

OpsGuard already has a topology graph endpoint and evidence-aware traces. The
next roadmap item is to connect those two surfaces so operators can visually see
which service, port, process, or config item is implicated during diagnosis.

## Goals

- Extend topology data with dynamic RCA annotations derived from real trace
  evidence and incident/session context.
- Highlight affected nodes, suspected root-cause candidates, downstream impact,
  and evidence-bearing relationships.
- Keep observed runtime facts visually distinct from inferred relationships.
- Support the existing nginx diagnosis path as the first MVP scenario.

## Non-Goals

- Do not build a new standalone topology product surface.
- Do not claim causality from a trace event alone.
- Do not infer relationships without marking them as inferred.
- Do not execute remediation or write tools from the topology view.

## Functional Requirements

1. Backend topology API can return annotations for a session or incident.
2. Trace/audit events should be converted into topology annotations when they
   mention or evidence:
   - service checks,
   - listening port checks,
   - config file checks,
   - process checks,
   - recent-change candidates.
3. Annotation fields should include:
   - target identifier,
   - target type: `service`, `port`, `process`, `config`, `host`, `unknown`,
   - RCA role: `affected`, `suspected_root_cause`, `downstream_impact`,
     `evidence`,
   - evidence summary,
   - source trace phase/event/tool,
   - execution state,
   - inferred flag.
4. Existing topology graph response should remain backward-compatible for
   current frontend consumers.
5. Frontend topology graph should render annotated nodes/edges distinctly:
   - affected node,
   - suspected root-cause candidate,
   - downstream impact,
   - inferred relationship.
6. Annotation failures or missing traces must be explicit empty results, not fake
   RCA highlights.

## Acceptance Criteria

- Given session trace events from an nginx diagnosis, topology annotations
  include nginx service, config file, listening port, and related process when
  the underlying evidence exists.
- Inferred relationships remain visibly distinct from observed runtime facts.
- No topology annotation claims execution unless backed by trace evidence with
  `execution_state` of `executed` or `failed`.
- Frontend build passes.

