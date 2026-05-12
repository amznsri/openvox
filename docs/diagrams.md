# OpenVox — Architecture Diagrams

Visual companion to [`architecture.md`](architecture.md). All diagrams use
[Mermaid](https://mermaid.js.org/) so they render natively on GitHub and in
most markdown viewers. Edit the source; the rendered version stays in sync.

For an explanation of *why* the design works (latency budget, sentence-flush
TTS, interruption handling), read `architecture.md`. This file shows
*what's wired to what*.

---

## 1. System overview — the three tiers

```mermaid
flowchart LR
    classDef ext fill:#3b1e54,stroke:#a78bfa,color:#e9d5ff
    classDef tier fill:#0f172a,stroke:#38bdf8,color:#e0f2fe
    classDef store fill:#064e3b,stroke:#34d399,color:#d1fae5
    classDef prov fill:#422006,stroke:#f59e0b,color:#fef3c7

    Browser[Browser / Dashboard user]:::ext
    Phone[Phone call · Twilio]:::ext
    WhatsApp[WhatsApp · Telegram]:::ext
    CLI[CLI / SDK / curl]:::ext

    subgraph Tier1["apps/dashboard · Next.js 14 + Tailwind · :3000"]
        Pages["App-Router pages: Playground · Agents · Templates · Skills · Schedules · Observability · Providers · Settings"]:::tier
    end

    subgraph Tier2["packages/server · Node 20 Fastify v5 · :3001"]
        Proxy["Transparent /api/v1/* proxy"]:::tier
        WSBridge["WS bridge /ws/voice ⇄ core"]:::tier
        Auth["JWT + rate-limit · multipart passthrough"]:::tier
    end

    subgraph Tier3["packages/core · Python 3.12 FastAPI + asyncio · :8000"]
        REST["REST routes: agents · sessions · skills · jobs · mcp · rag · playground · telephony"]:::tier
        VoiceWS["/ws/voice WebSocket"]:::tier
        Orchestrator[["pipeline/orchestrator.py · VoiceSession"]]:::tier
        Skills["Skill runner + 26 built-in skills"]:::tier
        Scheduler["APScheduler · 4 job kinds"]:::tier
        MCP["MCP session manager · stdio + SSE"]:::tier
        RAG["RAG store · embeddings or BM25"]:::tier
    end

    subgraph Stores["Persistence"]
        Postgres[("Postgres :5432<br/>agents · sessions · transcripts · documents · jobs")]:::store
        Redis[("Redis :6379")]:::store
        Files[("Local FS / MinIO / TOS<br/>audio + PDFs")]:::store
    end

    subgraph Providers["Pluggable providers"]
        BytePlus["BytePlus<br/>LLM · STT · TTS · RTC · TOS · RAG Cloud"]:::prov
        OpenAI["OpenAI · Anthropic · Gemini · DeepSeek"]:::prov
        STTAlt["Deepgram · AssemblyAI · Whisper"]:::prov
        TTSAlt["ElevenLabs · Cartesia · OpenAI TTS"]:::prov
        Telephony["Twilio · WhatsApp · Telegram"]:::prov
    end

    Browser --> Tier1
    CLI --> Tier2
    Tier1 -->|REST + WS| Tier2
    Phone --> Telephony --> Tier3
    WhatsApp --> Telephony

    Tier2 --> Tier3
    Tier3 --> Postgres
    Tier3 --> Redis
    Tier3 --> Files
    Tier3 -.->|HTTP / WS| BytePlus
    Tier3 -.-> OpenAI
    Tier3 -.-> STTAlt
    Tier3 -.-> TTSAlt
```

**Read the lanes top-to-bottom:** dashboard → gateway → core → persistence
and providers. The gateway is *transparent* — it doesn't transform payloads,
just adds auth, rate-limiting, multipart parsing, and a WS bridge. Almost
everything interesting happens in the core.

---

## 2. Voice pipeline — what happens during one call

```mermaid
sequenceDiagram
    autonumber
    participant U as User (mic / phone)
    participant WS as /ws/voice (FastAPI)
    participant OR as VoiceSession (orchestrator)
    participant STT as STT provider
    participant LLM as LLM provider
    participant SK as SkillRunner
    participant TTS as TTS provider
    participant DB as Postgres (sessions + transcripts)

    U->>WS: {type:"start", agent_id}
    WS->>DB: INSERT sessions (status=active)
    WS->>OR: build VoiceSession (load Agent row)
    OR->>OR: MCP servers connect (if any) → bridge skills

    loop while talking
        U-->>WS: PCM s16le binary frames @ 16 kHz
        WS->>OR: push_audio
        OR->>STT: transcribe_stream
        STT-->>OR: partial / final
        OR-->>WS: user_partial / user_final
    end

    OR->>LLM: chat_stream (tools = skill_specs)
    activate LLM
    LLM-->>OR: token deltas
    deactivate LLM
    OR->>OR: sentence-buffer → split on . ! ?

    par audio streams in parallel with LLM still tokening
        OR->>TTS: synthesize_stream(sentence)
        TTS-->>OR: PCM chunks (24 kHz)
        OR-->>WS: assistant_audio (binary)
    and LLM may emit tool_calls
        LLM-->>OR: tool_calls
        OR->>SK: invoke(name, args)
        SK-->>OR: result JSON
        OR-->>WS: skill_call + skill_result
        OR->>LLM: re-invoke with tool message
    end

    U->>WS: starts speaking → {interrupt}
    WS->>OR: cancel in-flight TTS

    U->>WS: {type:"end"} / disconnect
    WS->>DB: UPDATE sessions SET duration_ms, turn_count, first_token_ms, status=completed
```

**Three things make this fast:**
1. **Async generators** all the way down — no buffering between stages.
2. **Sentence-flush TTS** — audio starts streaming after the first
   `.` / `!` / `?`, not after the full LLM response.
3. **Interruption** — the user starting to speak cancels the in-flight TTS
   stream within one VAD frame.

---

## 3. Module map — where the code lives

```mermaid
flowchart TB
    classDef pkg fill:#1e293b,stroke:#38bdf8,color:#e0f2fe
    classDef core fill:#3b0764,stroke:#a78bfa,color:#f5f3ff

    subgraph Dashboard["apps/dashboard"]
        DApp["app/dashboard/* · 9 pages"]:::pkg
        DComp["components/{ui,nav,playground}/"]:::pkg
        DLib["lib/api.ts · voice/audio.ts"]:::pkg
    end

    subgraph Server["packages/server"]
        Sidx["index.ts · Fastify bootstrap"]:::pkg
        Sroutes["routes/{proxy,auth,health,telephony}.ts"]:::pkg
    end

    subgraph Core["packages/core/openvox"]
        cApi["api/{app.py, routes/*, ws/voice.py}"]:::core
        cPipe["pipeline/orchestrator.py"]:::core
        cProv["providers/{base, registry, bootstrap, byteplus/, openai_compat/}"]:::core
        cSkills["skills/{base, registry, runner, builtin/}"]:::core
        cRag["rag/{embeddings, store, bm25, extract, byteplus_cloud}"]:::core
        cStore["storage/{base, local, s3, byteplus_tos, factory}"]:::core
        cDb["db/{models, session}"]:::core
        cTel["telephony/{twilio, whatsapp, telegram}"]:::core
        cSched["scheduler/{registry, runner}"]:::core
        cMcp["mcp/{manager, bridge}"]:::core
        cUtil["utils/http.py · TLS-aware client"]:::core
    end

    SDKs["packages/sdk-ts · sdk-py"]:::pkg
    CLI["packages/cli · openvox CLI"]:::pkg

    DApp --> DLib --> Sroutes
    Sroutes --> cApi
    cApi --> cPipe & cSkills & cRag & cSched & cMcp
    cPipe --> cProv
    cSkills --> cProv
    cRag --> cProv & cStore
    cSched --> cApi
    cMcp --> cSkills
    cProv --> cUtil
    cApi --> cDb
    SDKs --> Sroutes
    CLI --> Sroutes
```

---

## 4. Skills, Templates, Scheduler, MCP — the extensibility surface

```mermaid
flowchart LR
    classDef builtin fill:#064e3b,stroke:#34d399,color:#d1fae5
    classDef tpl fill:#3b1e54,stroke:#a78bfa,color:#e9d5ff
    classDef job fill:#422006,stroke:#f59e0b,color:#fef3c7
    classDef mcp fill:#1e3a8a,stroke:#60a5fa,color:#dbeafe

    subgraph Skills["26 Built-in Skills — skills/builtin/"]
        S1["general · get_time · web_search · calculator"]:::builtin
        S2["ecommerce · lookup_order · check_stock · start_return · route_to_specialist"]:::builtin
        S3["education · explain_concept"]:::builtin
        S4["stock · get_quote · technical_indicators"]:::builtin
        S5["voice_analysis · sentiment · profanity · transcribe_recording"]:::builtin
        S6["documents · query_documents · analyze_image"]:::builtin
        S7["reception · business_info · check_availability · book · cancel · list_appointments"]:::builtin
        S8["sales · fetch_next_lead · record_disposition · book_demo · qualified_leads"]:::builtin
        S9["language · detect_language"]:::builtin
    end

    subgraph Templates["8 Templates — api/routes/templates.py"]
        T1["ecommerce-support"]:::tpl
        T2["education-tutor"]:::tpl
        T3["stock-analyst"]:::tpl
        T4["receptionist"]:::tpl
        T5["sales-sdr"]:::tpl
        T6["document-qa"]:::tpl
        T7["multilingual-support"]:::tpl
        T8["voice-analyzer"]:::tpl
    end

    subgraph Scheduler["Scheduler — 4 job kinds"]
        J1["agent_query · LLM call against an agent"]:::job
        J2["skill_run · direct skill invoke"]:::job
        J3["audio_batch · folder walk + transcribe + analyze"]:::job
        J4["outbound_call_batch · Twilio dial top-N leads"]:::job
    end

    subgraph MCPArea["MCP — per-agent external tool servers"]
        M1["stdio transport · spawned subprocess"]:::mcp
        M2["SSE transport · HTTP stream"]:::mcp
        Mbridge["bridge: wraps remote tools as<br/>BaseSkill with id 'mcp__server__tool'"]:::mcp
    end

    Agent["Agent row · system_prompt · skills · voice_map · mcp_servers"]
    SessionRun["VoiceSession at runtime"]

    T1 -. instantiate .-> Agent
    T2 -. instantiate .-> Agent
    T3 -. instantiate .-> Agent
    T4 -. instantiate .-> Agent
    T5 -. instantiate .-> Agent
    T6 -. instantiate .-> Agent
    T7 -. instantiate .-> Agent
    T8 -. instantiate .-> Agent

    Skills -- "referenced by id" --> Agent
    MCPArea -- "bridged at start" --> SessionRun
    Agent --> SessionRun
    Scheduler -- "agent_id" --> Agent
    Scheduler -- "skill_id" --> Skills
```

**The three extensibility points** (covered in `docs/extending.md`):

| Point | What you add | Where |
|---|---|---|
| Skill | A `BaseSkill` subclass with `id` / `description` / `parameters` / `run()` | `~/.openvox/skills/*.py` or `pip install` with `openvox.skills` entry-point |
| Provider | An `STTProvider` / `TTSProvider` / `LLMProvider` / `RTCProvider` subclass | `openvox.providers` entry-point |
| Template | A dict entry in `TEMPLATES` | `packages/core/openvox/api/routes/templates.py` |
| MCP server | Per-agent config: `{name, transport, command, args, env}` | Dashboard MCP tab on the agent edit page |

---

## 5. Data model — the tables behind every page

```mermaid
erDiagram
    Agent ||--o{ Session : "has many"
    Agent ||--o{ Document : "has many"
    Agent ||--o{ ScheduledJob : "owns"
    Session ||--o{ Transcript : "contains"
    Document ||--o{ DocumentChunk : "split into"
    ScheduledJob ||--o{ JobRun : "history"

    Agent {
        uuid id PK
        string name
        text system_prompt
        text greeting
        string llm_provider
        string llm_model
        string stt_provider
        string tts_provider
        string voice_id
        json voice_map
        float temperature
        int max_tokens
        json skills
        json mcp_servers
        string status
        datetime created_at
        datetime updated_at
    }
    Session {
        uuid id PK
        uuid agent_id FK
        string channel
        string caller_id
        int duration_ms
        int turn_count
        int first_token_ms
        int avg_response_ms
        float cost_usd
        string status
        datetime started_at
        datetime ended_at
    }
    Transcript {
        uuid id PK
        uuid session_id FK
        string role
        text text
        string skill_id
        json skill_args
        json skill_result
        string sentiment
        int started_ms
        int ended_ms
        datetime created_at
    }
    Document {
        uuid id PK
        uuid agent_id FK
        string filename
        string mime_type
        int byte_size
        string status
        datetime created_at
    }
    DocumentChunk {
        uuid id PK
        uuid document_id
        string agent_id
        int page
        text text
        vector embedding
        string kind
    }
    ScheduledJob {
        uuid id PK
        uuid agent_id FK
        string kind
        string trigger_type
        string trigger_value
        json payload
        bool enabled
        string last_status
        string last_error
        datetime last_run_at
    }
    JobRun {
        uuid id PK
        uuid job_id FK
        string status
        json result
        text error
        datetime started_at
        datetime ended_at
    }
```

**Cascade rules** (current — pre-Alembic, route-level):
- Delete `Agent` → cascade `Session` (built-in CASCADE), `Document` + `DocumentChunk` (in-route, Bug #30), `ScheduledJob` (in-route via FK), `JobRun` (in-route via FK on job).
- Delete `Session` → cascade `Transcript` (`cascade="all, delete-orphan"`).
- Delete `ScheduledJob` → cascade `JobRun` (in-route, Bug #29).

---

## 6. Provider plug-in surface — what swaps for what

```mermaid
flowchart TB
    classDef iface fill:#0f172a,stroke:#38bdf8,color:#e0f2fe
    classDef impl fill:#422006,stroke:#f59e0b,color:#fef3c7

    subgraph Interfaces["providers/base.py"]
        I1["LLMProvider · chat_stream"]:::iface
        I2["STTProvider · transcribe_stream"]:::iface
        I3["TTSProvider · synthesize_stream"]:::iface
        I4["RTCProvider · issue_token"]:::iface
    end

    subgraph LLMImpl["LLM implementations"]
        L1["byteplus · Ark Seed-2.0"]:::impl
        L2["openai · gpt-4o"]:::impl
        L3["anthropic · claude"]:::impl
        L4["gemini · OpenAI-compat endpoint"]:::impl
        L5["deepseek"]:::impl
    end

    subgraph STTImpl["STT implementations"]
        T1["byteplus · Seed ASR 2.0 streaming + batch"]:::impl
        T2["deepgram · WS"]:::impl
        T3["assemblyai · WS"]:::impl
        T4["whisper · HTTP / local"]:::impl
    end

    subgraph TTSImpl["TTS implementations"]
        V1["byteplus · Seed-Speech 2.0 unidirectional HTTP"]:::impl
        V2["elevenlabs · HTTP MP3 stream"]:::impl
        V3["cartesia · SSE PCM"]:::impl
        V4["openai · HTTP"]:::impl
    end

    subgraph RTCImpl["RTC implementations"]
        R1["byteplus · token signing only"]:::impl
    end

    I1 --- LLMImpl
    I2 --- STTImpl
    I3 --- TTSImpl
    I4 --- RTCImpl

    Registry["ProviderRegistry · is_available checks API key"]
    Bootstrap["bootstrap.register_builtins · registers all 14"]

    LLMImpl --> Registry
    STTImpl --> Registry
    TTSImpl --> Registry
    RTCImpl --> Registry
    Bootstrap --> Registry
```

**Key invariant**: every outbound HTTPS from a provider or skill goes through
`openvox/utils/http.py:make_async_client()` which honours
`OPENVOX_INSECURE_TLS` and `OPENVOX_EXTRA_CA_FILE`. Bare `httpx.AsyncClient`
in skills is a bug — see CLAUDE.md §8 #31.

---

## 7. Request paths cheat-sheet

| User action | Hits | Then |
|---|---|---|
| Visit dashboard | Next.js SSR / static | — |
| Open Playground / start voice call | `GET /ws/voice` (gateway → core WS bridge) | core `voice_ws()` → `VoiceSession` |
| Type message in Text tab | `POST /api/v1/playground/text` | core writes `Session` row, streams LLM tokens |
| Browse Templates | `GET /api/v1/templates` | static dict in `routes/templates.py` |
| "Use template" | `POST /api/v1/agents` (server-side template merge) | new `Agent` row |
| Upload a PDF | `POST /api/v1/agents/{id}/documents` (multipart) | core extracts → chunks → embeds-or-BM25 → stores |
| Ask a document question | `POST /api/v1/playground/document_query` | RAG retrieve → multimodal LLM call |
| Schedule a job | `POST /api/v1/jobs` | core writes `ScheduledJob` + registers with APScheduler |
| Phone call rings | Twilio webhook → `POST /api/v1/telephony/twilio/voice` | core returns TwiML pointing at Media Stream WS |
| Look at Observability | `GET /api/v1/sessions` | reads `Session` rows + computes aggregates client-side |
| Search in top bar | client-side fuzzy match over agents+templates+skills | no extra API call — uses cached SWR data |

---

## 8. Deployment topology — what runs where

```mermaid
flowchart LR
    classDef host fill:#3b1e54,stroke:#a78bfa,color:#e9d5ff
    classDef container fill:#0f172a,stroke:#38bdf8,color:#e0f2fe

    subgraph Host["Single machine · docker compose up"]
        direction TB
        subgraph Net["Docker network · openvox_default"]
            direction LR
            C1["openvox-dashboard<br/>node 20 alpine · :3000"]:::container
            C2["openvox-server<br/>node 20 alpine · :3001"]:::container
            C3["openvox-core<br/>python 3.12-slim + ffmpeg · :8000"]:::container
            C4["openvox-postgres<br/>:5432"]:::container
            C5["openvox-redis<br/>:6379"]:::container
            C6["openvox-minio<br/>:9000 :9001"]:::container
        end
        EnvFile[".env · BytePlus keys · OPENVOX_INSECURE_TLS"]:::host
        CaPem["docker/extra-ca.pem · corp Zscaler CA"]:::host
    end

    Browser((Browser)) --> C1
    C1 --> C2 --> C3
    C3 --> C4
    C3 --> C5
    C3 --> C6
    EnvFile -.->|reads at startup| C3
    CaPem -.->|mounted /etc/ssl/certs| C3
```

**Operationally** (covered in CLAUDE.md §9):
- Single-machine via `docker compose up --build`. SQLite-by-default
  fallback if Postgres isn't reachable.
- The user runs behind Zscaler — `OPENVOX_INSECURE_TLS=true` or
  populate `docker/extra-ca.pem`.
- BytePlus voice IDs must be activated per-key (catalogue at
  `docs.byteplus.com/en/docs/byteplusvoice/voicelist`); default that
  works on the user's key is `en_male_tim_uranus_bigtts`.

---

## 9. Where Session 7's polish lives in the diagram

| Fix | Diagram cell |
|---|---|
| TLS-aware HTTP for stock + web_search skills | §3 `cUtil` is now used by *every* `skills/builtin/*` |
| Session persistence | §2 step 2 (INSERT) + final step (UPDATE); §5 `Session` table |
| Top-bar search | §1 `Pages` cell uses cached SWR from `lib/api.ts` — §7 "Search in top bar" row |
| Template duplicate guard | §4 `T1..T8 -. instantiate .-> Agent` dotted edge is now confirm-gated |
| Cascade delete | §5 cascade-rules bullet list |
| Publish UX | §1 `Pages` cell only — pure dashboard fix |

---

## Source files for these diagrams

If a box looks wrong, the source of truth is always the code:

- §1 system overview → `docker-compose.yml` + `packages/{core,server}/`
- §2 pipeline sequence → `packages/core/openvox/pipeline/orchestrator.py`
- §3 module map → `packages/core/openvox/__init__.py` and the tree in `packages/core/`
- §4 extensibility → `skills/registry.py` + `api/routes/templates.py` + `scheduler/runner.py` + `mcp/`
- §5 data model → `packages/core/openvox/db/models.py`
- §6 providers → `packages/core/openvox/providers/{base,bootstrap,registry}.py`
- §7 request paths → `packages/core/openvox/api/routes/`
- §8 deployment → `docker-compose.yml` + `docker/`
