/**
 * lib/db-cloud-sql.ts — PostgreSQL (Cloud SQL) database adapter
 *
 * DROP-IN REPLACEMENT for lib/db.ts when running on Cloud Run with Cloud SQL.
 *
 * HOW TO ACTIVATE:
 *   1. Set DATABASE_URL in your Cloud Run service's environment variables.
 *      Cloud Run + Cloud SQL Unix socket format:
 *        postgresql://USER:PASSWORD@localhost/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE
 *
 *   2. Replace lib/db.ts with this file:
 *        cp lib/db-cloud-sql.ts lib/db.ts
 *
 *   3. Remove better-sqlite3 and add pg:
 *        npm uninstall better-sqlite3 @types/better-sqlite3
 *        npm install pg @types/pg
 *
 *   4. Remove the better-sqlite3 webpack external from next.config.js.
 *
 *   5. Run the schema migration SQL below against your Cloud SQL instance once.
 *
 * SCHEMA (run once against your Cloud SQL PostgreSQL instance):
 *   CREATE EXTENSION IF NOT EXISTS "pgcrypto";
 *
 *   CREATE TABLE IF NOT EXISTS users (
 *     id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
 *     username     TEXT UNIQUE NOT NULL,
 *     email        TEXT UNIQUE,
 *     password_hash TEXT NOT NULL,
 *     created_at   BIGINT NOT NULL
 *   );
 *
 *   CREATE TABLE IF NOT EXISTS sessions (
 *     id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
 *     user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 *     title      TEXT NOT NULL DEFAULT 'New Chat',
 *     created_at BIGINT NOT NULL,
 *     updated_at BIGINT NOT NULL
 *   );
 *   CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
 *
 *   CREATE TABLE IF NOT EXISTS messages (
 *     id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
 *     session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 *     role       TEXT NOT NULL CHECK(role IN ('user','assistant')),
 *     content    TEXT NOT NULL,
 *     created_at BIGINT NOT NULL
 *   );
 *   CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
 */

import { Pool } from 'pg';
import { v4 as uuidv4 } from 'uuid';

let pool: Pool | null = null;

function getPool(): Pool {
  if (!pool) {
    if (!process.env.DATABASE_URL) {
      throw new Error('DATABASE_URL environment variable is not set');
    }
    pool = new Pool({ connectionString: process.env.DATABASE_URL });
  }
  return pool;
}

export interface User {
  id: string;
  username: string;
  email?: string;
  password_hash: string;
  created_at: number;
}

export interface Session {
  id: string;
  user_id: string;
  title: string;
  created_at: number;
  updated_at: number;
}

export interface Message {
  id: string;
  session_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: number;
}

// ─── User Operations ──────────────────────────────────────────────────────────

export async function createUser(
  username: string,
  email: string | null,
  passwordHash: string
): Promise<User> {
  const id = uuidv4();
  const now = Date.now();
  await getPool().query(
    'INSERT INTO users (id, username, email, password_hash, created_at) VALUES ($1,$2,$3,$4,$5)',
    [id, username, email, passwordHash, now]
  );
  return { id, username, email: email || undefined, password_hash: passwordHash, created_at: now };
}

export async function getUserByUsername(username: string): Promise<User | undefined> {
  const { rows } = await getPool().query('SELECT * FROM users WHERE username = $1', [username]);
  return rows[0] as User | undefined;
}

export async function getUserById(id: string): Promise<User | undefined> {
  const { rows } = await getPool().query('SELECT * FROM users WHERE id = $1', [id]);
  return rows[0] as User | undefined;
}

// ─── Session Operations ───────────────────────────────────────────────────────

export async function createSession(userId: string, title = 'New Chat'): Promise<Session> {
  const id = uuidv4();
  const now = Date.now();
  await getPool().query(
    'INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES ($1,$2,$3,$4,$5)',
    [id, userId, title, now, now]
  );
  return { id, user_id: userId, title, created_at: now, updated_at: now };
}

export async function getSessionsByUser(userId: string): Promise<Session[]> {
  const { rows } = await getPool().query(
    'SELECT * FROM sessions WHERE user_id = $1 ORDER BY updated_at DESC',
    [userId]
  );
  return rows as Session[];
}

export async function getSessionById(id: string, userId: string): Promise<Session | undefined> {
  const { rows } = await getPool().query(
    'SELECT * FROM sessions WHERE id = $1 AND user_id = $2',
    [id, userId]
  );
  return rows[0] as Session | undefined;
}

export async function updateSessionTitle(id: string, userId: string, title: string): Promise<void> {
  await getPool().query(
    'UPDATE sessions SET title = $1, updated_at = $2 WHERE id = $3 AND user_id = $4',
    [title, Date.now(), id, userId]
  );
}

export async function touchSession(id: string): Promise<void> {
  await getPool().query('UPDATE sessions SET updated_at = $1 WHERE id = $2', [Date.now(), id]);
}

export async function deleteSession(id: string, userId: string): Promise<void> {
  await getPool().query('DELETE FROM sessions WHERE id = $1 AND user_id = $2', [id, userId]);
}

// ─── Message Operations ───────────────────────────────────────────────────────

export async function addMessage(
  sessionId: string,
  role: 'user' | 'assistant',
  content: string
): Promise<Message> {
  const id = uuidv4();
  const now = Date.now();
  await getPool().query(
    'INSERT INTO messages (id, session_id, role, content, created_at) VALUES ($1,$2,$3,$4,$5)',
    [id, sessionId, role, content, now]
  );
  return { id, session_id: sessionId, role, content, created_at: now };
}

export async function getMessagesBySession(sessionId: string): Promise<Message[]> {
  const { rows } = await getPool().query(
    'SELECT * FROM messages WHERE session_id = $1 ORDER BY created_at ASC',
    [sessionId]
  );
  return rows as Message[];
}
