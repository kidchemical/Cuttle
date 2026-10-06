# LEARNINGS

Corrections, knowledge gaps, and best practices. Format: `[LRN-YYYYMMDD-XXX] category`

| Situation | Action |
| --- | --- |
| User corrects you | Log with category `correction` |
| Knowledge was outdated | Log with category `knowledge_gap` |
| Found better approach | Log with category `best_practice` |
| Broadly applicable | Promote to AGENTS.md |

---
<!-- Add entries below -->

## [LRN-20260927-001] correction

**Priority:** high · **Status:** promoted (AGENTS.md, `.cuttle_global/rules/00-core.md`, `.cuttle_global/docs/git.md`) · **Area:** git / action forms

Agent pushed `origin/main` after a public-tree cleanup without an explicit “push”
ask (inferred from the user looking at GitHub leftovers). User: never push
without permission; in Cuttle chat always gate with a `git.push` action form
(not prose “OK to push?”). Git pending-changes UI remains the user’s own path.

**Metadata:** Cuttle project chip · `.cuttle_global/actions/git-push.yaml`

## [LRN-20260926-001] correction

**Priority:** high · **Status:** promoted (AGENTS.md, `.cuttle_global/rules/00-core.md`) · **Area:** action forms

Agent demoed Q&A by emitting a `choice` card and a `multi` card, both `resume: true`,
in one reply. The first click resumed the turn and orphaned the second card, so only
one answer ever arrived. Separately, each submit showed two user bubbles:
`/api/action-form/run` persisted the answer and the client's resume `sendMessage`
persisted it again via `/api/chat`.

- Rule: one Q&A card per reply; several questions → one `form` card.
- Code: `merge_qa_resume_specs` folds stray multi-card replies; run endpoint no longer
  persists; client resumes via `sendMessage({text})` (draft untouched); `[form-answers]`
  lists unanswered fields too.

**Metadata:** CH-000581 · `src/api/action_forms.py`, `src/api/web_chat_api.py`, `src/web/js/chat/chat_page.js`
