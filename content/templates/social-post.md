---
title: "{{title}}"
kind: social
channel: {{channel}}
mode: {{mode}}
status: draft
requires_human_review: true
generated_at: {{generated_at}}
suggested_community: {{community}}
---

{{body}}

{{link_line}}

---

**Not posted yet.** Posting needs an explicit approval either way:

    python agents/distribution/main.py approve --slug {{slug}}

On a channel set to `draft_only` a person posts it by hand; on one set
to `automatic` the distribution agent posts it after that approval,
following the rules of {{channel}}. Check before approving:

- [ ] the community allows this kind of link
- [ ] the post answers a real question instead of advertising
- [ ] self-promotion is disclosed as the community requires
- [ ] no duplicate of something already posted there
- [ ] every claim about a CVE matches its primary source
