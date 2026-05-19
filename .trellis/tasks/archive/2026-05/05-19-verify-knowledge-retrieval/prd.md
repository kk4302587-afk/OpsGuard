# Verify Real Knowledge Retrieval

## Problem

The trace panel shows "检索历史经验..." on each request, but a later request can show
"无相关历史经验" even when the knowledge base contains a clearly related saved entry
such as "重启Nginx服务".

Current suspicion: retrieval is technically called, but the matching logic is too
literal for Chinese operational text, so it behaves like a non-functional feature.

## Requirements

- Confirm whether knowledge retrieval and knowledge save perform real database I/O.
- Fix retrieval so semantically close Chinese/English operation requests can find
  saved knowledge entries, especially no-space Chinese text like "帮我重启nginx" vs
  "重启Nginx服务".
- Make the trace truthfully distinguish "searched but no match" from search failure.
- Reuse the same search implementation for the Agent and `/api/knowledge/search`.
- Audit other trace/API surfaces for the same class of issue: UI/trace claims a
  functional step exists, but the backend does not really perform that step.
- Add focused regression coverage that does not touch the host system.
