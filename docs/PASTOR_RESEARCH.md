# Pastor Pain Points Research

This document saves the research behind Marge's product direction. The useful takeaway is that Marge should not feel like another dashboard pastors have to manage. She should feel like a ministry-aware secretary who knows the pastor, knows the church context, connects to existing tools, and helps the pastor decide and act.

## Sources

### Lifeway: Greatest Needs Of Pastors

Source: https://research.lifeway.com/wp-content/uploads/2022/04/The-Greatest-Needs-of-Pastors-Phase-2-Quantitative-Report-Release-5.pdf

Useful findings:

- Time management was the largest personal-life need in the report.
- Work/home balance and avoiding over-commitment are major pastor pain points.
- Consistent rest, exercise, and hobbies are hard for many pastors to maintain.

Product implication:

- Marge should protect time and reduce follow-up load, not simply show more things to do.
- Calendar-aware assistance matters because care work competes with sermon prep, meetings, family, and emergencies.

### Barna: Pastors Considering Quitting

Source: https://www.barna.com/research/pastors-quitting-ministry/

Useful findings:

- In March 2022, Barna reported 42% of pastors had considered quitting full-time ministry in the prior year.
- Stress, loneliness/isolation, and political division were the major reasons surfaced.

Product implication:

- Marge should feel personal and supportive, not corporate.
- The first-run experience should ask about the pastor's ministry context, emotional load, role, staff reality, and what kinds of work are currently overwhelming.

### Wesleyan Report: A Burden Too Heavy?

Source: https://cdn.resources.wesleyan.org/wesleyanrc/wp-content/uploads/FIM-Report-Workload.pdf

Useful findings:

- Pastoral work involves costly context switching between meetings, finances, care, emergencies, email, and deadlines.
- Unexpected events interrupt normal ministry work.
- Pastors face fast expectations for calls and emails.
- One pastor described daily email load as a never-ending battle.

Product implication:

- Chat should be the main interface because pastors need to delegate messy, cross-system work.
- Marge should triage and draft email, propose calendar blocks, and connect the action back to people and care history.

### Planning Center API And Integrations

Sources:

- https://api.planningcenteronline.com/docs/overview/getting-started
- https://www.planningcenter.com/integrations

Useful findings:

- Planning Center exposes a REST API for church account data.
- Planning Center's product posture already supports connected tool ecosystems.

Product implication:

- Marge should integrate with Planning Center rather than replace it.
- Planning Center can remain the system of record for People, Calendar, Groups, Services, Check-Ins, and related data.

### Rock RMS API

Source: https://community.rockrms.com/api-docs/

Useful findings:

- Rock exposes API resources for integrations.

Product implication:

- Rock can be a first-class data source for members, attendance, groups, notes, and eventual approved writeback.

## Product Thesis

Pastors do not need a prettier CRM first. They need a pastoral secretary who gets them:

- Knows their church and role.
- Knows their ministry load and weekly rhythm.
- Connects to the tools they already use.
- Surfaces the few things that need action now.
- Drafts the hard first pass.
- Waits for approval before sending or changing external systems.
- Remembers pastoral context without exposing private prayer or counseling details.

## First-Run Experience Requirements

When a pastor first signs up, Marge should ask:

- What is your name and church?
- What is your role?
- About how many people attend weekly?
- What tools do you already use?
- Where does follow-up currently break down?
- What kinds of pastoral care feel heaviest right now?
- What should Marge sound like when she drafts for you?
- What days/times should Marge protect?
- What should Marge never do without asking?

The goal is not generic setup. The goal is for the pastor to feel, "Marge understands my ministry and is already trying to help."
