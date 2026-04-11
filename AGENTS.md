# AGENTS.md

## Mission
This repository supports Marge, the AI pastoral assistant for churches.

The mission is to help pastors and church staff know who needs care today, follow up consistently, and prevent people from falling through the cracks.

## Product context
- ICP: pastors, executive pastors, pastoral admins, and church staff at churches that need better care follow-through without enterprise-software complexity
- Core promise: Marge gives pastors a warm, practical daily care assistant that surfaces who needs attention, drafts outreach in their voice, tracks care situations, and keeps follow-up moving
- Current priorities:
  1. make the workflow-first staging experience truly pilot-ready
  2. tighten pastoral operations, safety, and onboarding/runbook clarity
  3. convert working product flows into credible pilot sales and onboarding rails

## General operating rules
- Operate proactively.
- Convert founder input into roadmap updates, tasks, and execution.
- Prefer momentum through small, bounded tasks.
- Prefer reversible changes over broad rewrites.
- Keep documentation aligned with reality.
- Create follow-up tasks whenever work is deferred or partially completed.
- Minimize unnecessary confirmations.
- Respect the pastoral sensitivity of care data and recommendations.

## Commands
- Install: `pip install -r requirements.txt`
- Dev API: `uvicorn app.main:app --reload`
- Test: `pytest -q`
- Build/verify: `python3 -m compileall app`
- Morning briefing: `python3 scripts/morning_briefing.py`

## Definition of done
A task is done only when:
- the implementation is complete
- relevant checks/tests pass
- docs are updated if reality changed
- PR or summary explains what changed and why
- follow-up tasks are created for anything deferred

## Approval boundaries
Require approval before:
- production deploys
- pricing changes
- public posting or sending emails/messages externally
- deleting real church/customer data
- risky auth or billing changes
- legal/policy/customer-facing commitment changes
- external pastoral/customer outreach

## Safe autonomous actions
The agent may do these without asking:
- create or update internal docs
- create and reprioritize backlog items
- perform research
- draft copy and content briefs
- fix low-risk bugs
- add tests
- refactor low-risk code
- open PRs
- improve internal developer experience
- improve staging/pilot-readiness documentation

## Review checklist
For each meaningful change, verify:
- correctness
- regression risk
- test/check coverage where practical
- clarity of naming and structure
- docs updated if needed
- pastoral/privacy implications considered
- rollback risk understood

## Documentation rules
Maintain these files as part of the operating layer:
- docs/VISION.md
- docs/ROADMAP.md
- docs/BACKLOG.md
- docs/DECISIONS.md
- docs/METRICS.md
- docs/MARKETING.md
- docs/RESEARCH.md
- docs/DAILY_DIGEST.md

## Daily digest format
Provide a concise digest with:
- shipped
- in progress
- blocked
- approvals needed
- recommended next focus

## Priority order
When choosing work, generally prioritize:
1. pilot readiness and user-facing care workflow quality
2. revenue-enabling improvements
3. pastoral safety and reliability
4. onboarding/distribution/marketing leverage
5. documentation and cleanup

## Execution style
- Do not wait passively if safe work exists.
- Do not endlessly plan without shipping.
- Break large goals into smaller bounded tasks.
- Keep PRs and change sets reviewable.
- Treat staging as the proving ground before production.
