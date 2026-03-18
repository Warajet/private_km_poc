/**
 * lib/db.ts — SQLite adapter (local development)
 *
 * All functions are async so this file shares an identical interface with
 * lib/db-cloud-sql.ts (PostgreSQL). Dockerfile.production swaps this file
 * with db-cloud-sql.ts before building the production image — zero route
 * changes required.
 *
 * The `async` keyword on each function automatically wraps the synchronous
 * better-sqlite3 return value in a resolved Promise.
 */

import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';
import { v4 as uuidv4 } from 'uuid';

const DB_DIR = path.join(process.cwd(), 'data');
const DB_PATH = path.join(DB_DIR, 'chat.db');

let db: Database.Database | null = null;

function getDb(): Database.Database {
  if (!db) {
    if (!fs.existsSync(DB_DIR)) {
      fs.mkdirSync(DB_DIR, { recursive: true });
    }
    db = new Database(DB_PATH);
    db.pragma('journal_mode = WAL');
    db.pragma('foreign_keys = ON');
    initDb(db);
  }
  return db;
}

function initDb(database: Database.Database) {
  database.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id           TEXT PRIMARY KEY,
      username     TEXT UNIQUE NOT NULL,
      email        TEXT UNIQUE,
      password_hash TEXT NOT NULL,
      created_at   INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
      id         TEXT PRIMARY KEY,
      user_id    TEXT NOT NULL,
      title      TEXT NOT NULL DEFAULT 'New Chat',
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS messages (
      id         TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      role       TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
      content    TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_user_id    ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
  `);
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
  getDb()
    .prepare(
      'INSERT INTO users (id, username, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)'
    )
    .run(id, username, email, passwordHash, now);
  return { id, username, email: email || undefined, password_hash: passwordHash, created_at: now };
}

export async function getUserByUsername(username: string): Promise<User | undefined> {
  return getDb()
    .prepare('SELECT * FROM users WHERE username = ?')
    .get(username) as User | undefined;
}

export async function getUserById(id: string): Promise<User | undefined> {
  return getDb()
    .prepare('SELECT * FROM users WHERE id = ?')
    .get(id) as User | undefined;
}

// ─── Session Operations ───────────────────────────────────────────────────────

export async function createSession(userId: string, title = 'New Chat'): Promise<Session> {
  const id = uuidv4();
  const now = Date.now();
  getDb()
    .prepare(
      'INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)'
    )
    .run(id, userId, title, now, now);
  return { id, user_id: userId, title, created_at: now, updated_at: now };
}

export async function getSessionsByUser(userId: string): Promise<Session[]> {
  return getDb()
    .prepare('SELECT * FROM sessions WHERE user_id = ? ORDER BY updated_at DESC')
    .all(userId) as Session[];
}

export async function getSessionById(id: string, userId: string): Promise<Session | undefined> {
  return getDb()
    .prepare('SELECT * FROM sessions WHERE id = ? AND user_id = ?')
    .get(id, userId) as Session | undefined;
}

export async function updateSessionTitle(id: string, userId: string, title: string): Promise<void> {
  getDb()
    .prepare('UPDATE sessions SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?')
    .run(title, Date.now(), id, userId);
}

export async function touchSession(id: string): Promise<void> {
  getDb()
    .prepare('UPDATE sessions SET updated_at = ? WHERE id = ?')
    .run(Date.now(), id);
}

export async function deleteSession(id: string, userId: string): Promise<void> {
  getDb()
    .prepare('DELETE FROM sessions WHERE id = ? AND user_id = ?')
    .run(id, userId);
}

// ─── Message Operations ───────────────────────────────────────────────────────

export async function addMessage(
  sessionId: string,
  role: 'user' | 'assistant',
  content: string
): Promise<Message> {
  const id = uuidv4();
  const now = Date.now();
  getDb()
    .prepare(
      'INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)'
    )
    .run(id, sessionId, role, content, now);
  return { id, session_id: sessionId, role, content, created_at: now };
}

export async function getMessagesBySession(sessionId: string): Promise<Message[]> {
  return getDb()
    .prepare('SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC')
    .all(sessionId) as Message[];
}
