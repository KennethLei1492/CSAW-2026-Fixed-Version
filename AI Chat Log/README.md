# AI Chat Log

AI interaction logs for the submission (required by the qualifier: *"GenAI Transcripts:
Comprehensive logs ... of all prompts and AI responses used to generate the Trojan"*).

The team (**GOONIES** — Kenneth Lei, Jessica Ryn, Kyson Wu, Kevin Wu) worked across
multiple LLMs — Claude (Opus 4.7/4.8, Fable 5.1) via the Claude Code agentic CLI and
Codex/ChatGPT (GPT-5.6 Terra, GPT-6 Astra, Luna 6) — including every attempt, success,
and failure.

## Contents

### Raw session exports (zipped `transcript.jsonl` + metadata)
- **`Main Log for Main GitHub.zip`** — the primary build session behind the submitted
  Trojan (full raw export: `transcript.jsonl`, subagent transcript, and metadata).
- **`Replication Test 1.zip`** — raw export of the first clean-room replication run.
- **`Replication Test 2.zip`** — raw export of the second clean-room replication run.

### Human-readable transcripts
- **`Human Readable Transformation of JSON Zip Files.zip`** — the three raw sessions above
  converted to readable Markdown conversations (`Main_Log_for_Main_GitHub__conversation.md`,
  `Replication_Test_1__conversation.md`, `Replication_Test_2__conversation.md`).
- **`Documentation_Session_Claude_Opus_4_8.md`** — human-readable log of the Claude Opus 4.8
  session that merged the team documentation into the repository README and assembled this
  AI Chat Log folder (verbatim user prompt + assistant responses).

### Chat link history
- **`CSAW26_CHAT_LINKS.pdf`** — index of every chat link across team members, model version
  & effort, and topic (including failed attempts), for CSAW '26.

## Related documentation
The methodology, verbatim teammate prompts, and the AI decision record are also summarized
in the repository's main `README.md` (Section 5 "AI methodology" and Section 7 "Team
Documentation"), in `ai/ai_transcript.md`, and in `build/verification_report.json`
(per-stage RAG `decision` fields).

## Repository
https://github.com/KennethLei1492/CSAW26
