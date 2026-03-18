# Private KM — Organization AI Chatbot

A ChatGPT-like internal chatbot that lets every employee in your organization talk to **Gemini** through a **single shared API subscription**, while keeping each user's conversation history completely private and isolated from others.

---

## Table of Contents

- [Overview](#overview)
- [Key Design Goal](#key-design-goal)
- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
- [Environment Variables](#environment-variables)
- [Getting Started](#getting-started)
- [How Subscription Sharing Works](#how-subscription-sharing-works)
- [Security Model](#security-model)
- [Tech Stack](#tech-stack)
- [Development Notes](#development-notes)

---

## Overview

Private KM is a full-stack web application built with **Next.js 14** that serves as an internal AI assistant portal for organizations. Users log in with their own credentials and get a personal chat workspace. All requests are routed through a single Gemini API key on the server, so the organization only needs **one Gemini Enterprise subscription** regardless of how many employees use the tool.

---

## Key Design Goal

> **Minimize Gemini Enterprise subscriptions while giving every user their own isolated chat history.**

| Concern | How it is solved |
|---|---|
| Cost | One `GEMINI_API_KEY` on the server; all users share it |
| Privacy | Every session is owned by a `user_id`; the API enforces ownership on every request |
| History | Conversation history is stored in a local SQLite database, scoped per user |
| Statelessness | Gemini itself is stateless — we reconstruct full conversation history on each request |

Example: User A creates sessions 1 and 2. User B creates session 3. The Gemini API receives three independent conversation threads, but the server ensures User A can never read or write to session 3, and vice-versa.

---

## Features

- **ChatGPT-style UI** — dark theme, sidebar, streaming responses token by token
- **Per-user chat history** — sessions grouped by Today / Yesterday / Last 7 Days / Older
- **Real-time streaming** — AI response appears word-by-word using `ReadableStream`
- **Markdown rendering** — bold, italic, tables, lists, blockquotes
- **Syntax-highlighted code blocks** — with a one-click copy button per block
- **Auto-titled sessions** — first user message becomes the session title automatically
- **Login / self-registration** — organization members create their own accounts
- **Delete conversations** — two-click confirm to prevent accidents
- **Quick suggestion chips** — on the welcome screen to get started fast
- **Mobile responsive** — collapsible sidebar for small screens
- **No file uploads** — intentionally omitted to keep the scope focused

---

## Architecture

```
Browser  ──►  Next.js Middleware (JWT check)
              │
              ├─► /login            Public — no auth required
              ├─► /api/auth/*       Public — login / register / logout
              │
              └─► /api/sessions/*   Protected — scoped to authenticated user
              └─► /api/chat         Protected — streams Gemini response
                        │
                        ├── Loads user's message history from SQLite
                        ├── Calls Gemini API with reconstructed history
                        └── Streams response back; saves to SQLite when done
```

### Request lifecycle for a chat message

```
1. User types a message and presses Enter
2. Browser POSTs { sessionId, message } to /api/chat
3. Middleware validates the JWT cookie — rejects if invalid
4. Route handler verifies the session belongs to this user (ownership check)
5. Full message history for this session is loaded from SQLite
6. User message is saved to SQLite immediately
7. Gemini API is called with the reconstructed history + new message
8. The AI response is streamed chunk-by-chunk back to the browser
9. When the stream ends, the complete AI response is saved to SQLite
10. Browser displays each chunk as it arrives (streaming cursor effect)
```

---

## Project Structure

```
private_km_poc/
│
├── app/                          # Next.js App Router
│   ├── layout.tsx                # Root HTML shell
│   ├── page.tsx                  # Redirects / → /chat
│   ├── globals.css               # Tailwind base + custom animations
│   │
│   ├── login/
│   │   └── page.tsx              # Login & registration page (client component)
│   │
│   ├── chat/
│   │   └── page.tsx              # Main chat UI (client component)
│   │
│   └── api/                      # REST API routes (server-only)
│       ├── auth/
│       │   ├── login/route.ts    # POST — verify credentials, set JWT cookie
│       │   ├── logout/route.ts   # POST — clear JWT cookie
│       │   ├── register/route.ts # POST — create new user account
│       │   └── me/route.ts       # GET  — return current user info
│       │
│       ├── sessions/
│       │   ├── route.ts          # GET list / POST create session
│       │   └── [id]/
│       │       ├── route.ts          # GET one / DELETE session
│       │       └── messages/route.ts # GET all messages in session
│       │
│       └── chat/
│           └── route.ts          # POST — stream Gemini response
│
├── components/
│   ├── Sidebar.tsx               # Session list, new chat, logout
│   ├── MessageBubble.tsx         # Renders user or AI message with Markdown
│   └── ChatInput.tsx             # Auto-resizing textarea + send button
│
├── lib/
│   ├── db.ts                     # SQLite schema, connection, CRUD helpers
│   ├── auth.ts                   # JWT sign/verify, bcrypt, cookie helpers
│   └── gemini.ts                 # Gemini client, history reconstruction
│
├── middleware.ts                 # Edge middleware — JWT auth guard
├── next.config.js                # Webpack externals for better-sqlite3
├── tailwind.config.js
├── tsconfig.json
├── postcss.config.js
├── .env.example                  # Environment variable template
└── .gitignore
```

---

## Database Schema

The SQLite database is created automatically at `./data/chat.db` on first run.

```sql
-- Organization users
CREATE TABLE users (
  id           TEXT PRIMARY KEY,   -- UUID v4
  username     TEXT UNIQUE NOT NULL,
  email        TEXT UNIQUE,        -- optional
  password_hash TEXT NOT NULL,     -- bcrypt hash (10 rounds)
  created_at   INTEGER NOT NULL    -- Unix timestamp (ms)
);

-- Chat sessions (one per conversation thread)
CREATE TABLE sessions (
  id         TEXT PRIMARY KEY,     -- UUID v4
  user_id    TEXT NOT NULL,        -- owner — FK → users.id
  title      TEXT NOT NULL DEFAULT 'New Chat',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,     -- bumped on every new message
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Individual messages within a session
CREATE TABLE messages (
  id         TEXT PRIMARY KEY,     -- UUID v4
  session_id TEXT NOT NULL,        -- FK → sessions.id
  role       TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Indexes for fast per-user and per-session lookups
CREATE INDEX idx_sessions_user_id    ON sessions(user_id);
CREATE INDEX idx_messages_session_id ON messages(session_id);
```

**Cascade deletes:** deleting a user removes all their sessions; deleting a session removes all its messages.

---

## API Reference

All protected endpoints require the `auth_token` httpOnly cookie (set automatically after login).

### Auth

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| `POST` | `/api/auth/register` | `{ username, password, email? }` | Create a new account |
| `POST` | `/api/auth/login` | `{ username, password }` | Sign in, receive JWT cookie |
| `POST` | `/api/auth/logout` | — | Clear the JWT cookie |
| `GET`  | `/api/auth/me` | — | Return `{ userId, username }` for the current session |

### Sessions

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`    | `/api/sessions` | List all sessions for the authenticated user, newest first |
| `POST`   | `/api/sessions` | Create a new empty session |
| `GET`    | `/api/sessions/:id` | Get a single session (must be owned by caller) |
| `DELETE` | `/api/sessions/:id` | Delete a session and all its messages |
| `GET`    | `/api/sessions/:id/messages` | Get all messages in a session, oldest first |

### Chat

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| `POST` | `/api/chat` | `{ sessionId, message }` | Send a message; returns a streaming plain-text response |

The `/api/chat` endpoint returns `Content-Type: text/plain` and streams the AI response token-by-token. The client reads it with `fetch` + `ReadableStream`.

---

## Environment Variables

Copy `.env.example` to `.env.local` and fill in your values:

```env
# Required — Gemini API key from Google AI Studio or Vertex AI
GEMINI_API_KEY=your_gemini_api_key_here

# Optional — which Gemini model to use (default: gemini-1.5-pro)
GEMINI_MODEL=gemini-1.5-pro

# Required in production — long random string for signing JWTs
JWT_SECRET=change-this-to-a-long-random-secret-in-production

# Optional — customize the AI assistant's persona / instructions
SYSTEM_PROMPT=You are a helpful assistant for our organization.
```

**Getting a Gemini API key:**
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create a new API key
3. Paste it as `GEMINI_API_KEY` in `.env.local`

For **Vertex AI / Gemini Enterprise**, replace `@google/generative-ai` usage in `lib/gemini.ts` with `@google-cloud/vertexai` and configure service account credentials accordingly.

---

## Getting Started

### Prerequisites

- Node.js 18 or later
- npm 9 or later

### Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd private_km_poc

# 2. Install dependencies
npm install

# 3. Set up environment variables
cp .env.example .env.local
# Edit .env.local and set GEMINI_API_KEY and JWT_SECRET

# 4. Start the development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

- You will be redirected to `/login` automatically
- Click **Register** to create the first account
- Start chatting

### Production build

```bash
npm run build
npm start
```

The SQLite database file is created automatically at `./data/chat.db` on first startup. Make sure the process has write access to the project directory.

---

## How Subscription Sharing Works

This is the core innovation of the project. The diagram below shows how a single Gemini API key serves multiple users without mixing their data:

```
User A — Session 1          User B — Session 3
  msg: "Hello"                msg: "Translate this"
  msg: "Explain X"            msg: "Thanks"
         │                           │
         ▼                           ▼
   ┌─────────────────────────────────────────┐
   │         Private KM Server               │
   │                                          │
   │  1. Authenticate user (JWT)              │
   │  2. Verify session ownership (user_id)   │
   │  3. Load THIS user's message history     │
   │  4. Reconstruct Gemini chat history      │
   │  5. Call Gemini API ──────────────────►  │
   │  6. Stream response back to user         │
   │  7. Save response to SQLite              │
   └─────────────────────────────────────────┘
                     │
                     ▼
            One Gemini API Key
         (shared, but stateless)
```

Gemini itself has no concept of "users" or "sessions" in our system — it simply receives a list of messages and returns a response. The server is the sole keeper of who owns what. This means:

- **Zero cost per user** on the Gemini side — only one API key/billing account needed
- **Full isolation** — a bug or misconfiguration cannot leak one user's history to another because every query includes `WHERE user_id = ?`
- **Scalable** — adding 100 more users costs nothing extra in terms of Gemini subscriptions

---

## Security Model

| Threat | Mitigation |
|---|---|
| Unauthenticated access | `middleware.ts` — JWT verified on every non-public route at the edge |
| Session/message cross-access | All DB queries include `AND user_id = ?` — a user cannot read another user's data even with a valid JWT |
| Password storage | bcrypt with 10 salt rounds — plaintext passwords never stored |
| JWT tampering | HS256 signed with `JWT_SECRET`; invalid or expired tokens redirect to `/login` and clear the cookie |
| XSS via AI content | Markdown is rendered via `react-markdown` with no `dangerouslySetInnerHTML`; raw HTML in AI responses is not executed |
| SQL injection | All queries use `better-sqlite3` prepared statements with `?` parameters |

**Important for production:**
- Set a strong, random `JWT_SECRET` (at least 32 characters)
- Run behind HTTPS so the `httpOnly` cookie cannot be intercepted
- Consider rate-limiting `/api/chat` and `/api/auth/login` to prevent abuse

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | [Next.js 14](https://nextjs.org) (App Router) |
| Language | TypeScript |
| Styling | [Tailwind CSS](https://tailwindcss.com) |
| Database | [SQLite](https://www.sqlite.org) via [better-sqlite3](https://github.com/WiseLibs/better-sqlite3) |
| AI | [Google Generative AI SDK](https://github.com/google-gemini/generative-ai-js) (`@google/generative-ai`) |
| Auth | [jose](https://github.com/panva/jose) (JWT) + [bcryptjs](https://github.com/dcodeIO/bcrypt.js) |
| Markdown | [react-markdown](https://github.com/remarkjs/react-markdown) + [remark-gfm](https://github.com/remarkjs/remark-gfm) |
| Syntax highlighting | [react-syntax-highlighter](https://github.com/react-syntax-highlighter/react-syntax-highlighter) (Prism / One Dark theme) |
| Icons | [lucide-react](https://lucide.dev) |
| IDs | [uuid](https://github.com/uuidjs/uuid) (v4) |

---

## Development Notes

### Why SQLite?

For a POC / internal tool with a single server process, SQLite is ideal — zero infrastructure, zero configuration, and the WAL journal mode makes concurrent reads fast. For a multi-instance deployment, replace `lib/db.ts` with a PostgreSQL or MySQL client.

### Why not store sessions in Gemini?

Google's Gemini API is stateless per request. Even if a "conversation ID" feature were available, binding users to external AI-side sessions would defeat the cost-sharing goal: you would need one Gemini account per user. By owning the history in our DB, we get full control and full isolation at no extra cost.

### Switching to Vertex AI (Gemini Enterprise)

1. Install `@google-cloud/vertexai` instead of `@google/generative-ai`
2. Update `lib/gemini.ts` to use `VertexAI` with your project ID and location
3. Configure service account credentials (`GOOGLE_APPLICATION_CREDENTIALS`)
4. The rest of the application (auth, DB, UI) is unchanged

### Adding more Gemini models

Change `GEMINI_MODEL` in `.env.local` to any supported model name, e.g.:
- `gemini-1.5-flash` — faster and cheaper for simpler tasks
- `gemini-1.5-pro` — default, best quality
- `gemini-2.0-flash` — latest generation (if available on your key)
