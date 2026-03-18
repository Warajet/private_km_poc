'use client';

import { useState } from 'react';

export interface SessionItem {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
}

interface Props {
  sessions: SessionItem[];
  currentSessionId: string | null;
  username: string;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onLogout: () => void;
}

function groupSessionsByDate(sessions: SessionItem[]) {
  const now = Date.now();
  const ONE_DAY = 24 * 60 * 60 * 1000;

  const groups: { label: string; items: SessionItem[] }[] = [
    { label: 'Today', items: [] },
    { label: 'Yesterday', items: [] },
    { label: 'Previous 7 Days', items: [] },
    { label: 'Older', items: [] },
  ];

  for (const s of sessions) {
    const diff = now - s.updated_at;
    if (diff < ONE_DAY) {
      groups[0].items.push(s);
    } else if (diff < 2 * ONE_DAY) {
      groups[1].items.push(s);
    } else if (diff < 7 * ONE_DAY) {
      groups[2].items.push(s);
    } else {
      groups[3].items.push(s);
    }
  }

  return groups.filter((g) => g.items.length > 0);
}

export function Sidebar({
  sessions,
  currentSessionId,
  username,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onLogout,
}: Props) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const grouped = groupSessionsByDate(sessions);

  function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (confirmDeleteId === id) {
      onDeleteSession(id);
      setConfirmDeleteId(null);
    } else {
      setConfirmDeleteId(id);
      // Auto-cancel after 3 seconds
      setTimeout(() => setConfirmDeleteId(null), 3000);
    }
  }

  return (
    <aside className="flex flex-col w-64 flex-shrink-0 bg-[#171717] h-full overflow-hidden">
      {/* Header */}
      <div className="p-3">
        <div className="flex items-center gap-2 px-2 py-1.5 mb-1">
          <div className="w-6 h-6 rounded bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center flex-shrink-0">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5">
              <path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z" />
              <path d="M8 12h8M12 8v8" />
            </svg>
          </div>
          <span className="text-white text-sm font-semibold">Private KM</span>
        </div>

        {/* New Chat button */}
        <button
          onClick={onNewChat}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[#ececec] hover:bg-[#2a2a2a] text-sm transition-colors group"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="text-[#8e8ea0] group-hover:text-white flex-shrink-0"
          >
            <path d="M12 5v14M5 12h14" />
          </svg>
          New Chat
        </button>
      </div>

      {/* Session list */}
      <nav className="flex-1 overflow-y-auto px-3 pb-2">
        {sessions.length === 0 ? (
          <div className="text-center text-[#555] text-xs mt-8 px-4">
            No conversations yet.
            <br />
            Start a new chat!
          </div>
        ) : (
          grouped.map((group) => (
            <div key={group.label} className="mb-4">
              <p className="text-[#555] text-xs font-medium px-2 mb-1">{group.label}</p>
              {group.items.map((session) => {
                const isActive = session.id === currentSessionId;
                const isHovered = hoveredId === session.id;
                const isConfirm = confirmDeleteId === session.id;

                return (
                  <div
                    key={session.id}
                    onClick={() => onSelectSession(session.id)}
                    onMouseEnter={() => setHoveredId(session.id)}
                    onMouseLeave={() => setHoveredId(null)}
                    className={`group relative flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors text-sm mb-0.5 ${
                      isActive
                        ? 'bg-[#2a2a2a] text-white'
                        : 'text-[#ececec] hover:bg-[#212121]'
                    }`}
                  >
                    <span className="flex-1 truncate text-[13px]">{session.title}</span>

                    {/* Delete button */}
                    {(isHovered || isActive || isConfirm) && (
                      <button
                        onClick={(e) => handleDelete(e, session.id)}
                        title={isConfirm ? 'Click again to confirm delete' : 'Delete conversation'}
                        className={`flex-shrink-0 p-1 rounded transition-colors ${
                          isConfirm
                            ? 'text-red-400 hover:text-red-300'
                            : 'text-[#555] hover:text-[#8e8ea0]'
                        }`}
                      >
                        {isConfirm ? (
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M20 6L9 17l-5-5" />
                          </svg>
                        ) : (
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                          </svg>
                        )}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          ))
        )}
      </nav>

      {/* Footer: user info + logout */}
      <div className="p-3 border-t border-[#2a2a2a]">
        <div className="flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-[#2a2a2a] transition-colors group">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-green-500 to-emerald-600 flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
            {username.slice(0, 1).toUpperCase()}
          </div>
          <span className="flex-1 text-[#ececec] text-sm truncate">{username}</span>
          <button
            onClick={onLogout}
            title="Sign out"
            className="text-[#555] hover:text-[#8e8ea0] transition-colors opacity-0 group-hover:opacity-100"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
            </svg>
          </button>
        </div>
      </div>
    </aside>
  );
}
