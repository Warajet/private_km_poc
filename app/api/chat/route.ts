import { NextResponse } from 'next/server';
import { getAuthFromRequest } from '@/lib/auth';
import {
  getSessionById,
  getMessagesBySession,
  addMessage,
  updateSessionTitle,
  touchSession,
} from '@/lib/db';
import { streamChatResponse } from '@/lib/gemini';

export async function POST(request: Request) {
  const auth = await getAuthFromRequest(request);
  if (!auth) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  let sessionId: string, message: string;
  try {
    ({ sessionId, message } = await request.json());
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }

  if (!sessionId || !message?.trim()) {
    return NextResponse.json({ error: 'sessionId and message are required' }, { status: 400 });
  }

  // Verify ownership — users only access their own sessions
  const session = getSessionById(sessionId, auth.userId);
  if (!session) {
    return NextResponse.json({ error: 'Session not found' }, { status: 404 });
  }

  // Load this user's conversation history for this session
  const history = getMessagesBySession(sessionId);

  // Persist user message immediately
  addMessage(sessionId, 'user', message.trim());

  // Auto-title the session from the first user message
  if (history.length === 0) {
    const title =
      message.trim().length > 60
        ? message.trim().slice(0, 60) + '…'
        : message.trim();
    updateSessionTitle(sessionId, auth.userId, title);
  }

  touchSession(sessionId);

  try {
    const streamResult = await streamChatResponse(history, message.trim());

    let fullResponse = '';

    const readableStream = new ReadableStream({
      async start(controller) {
        try {
          for await (const chunk of streamResult.stream) {
            const text = chunk.text();
            fullResponse += text;
            controller.enqueue(new TextEncoder().encode(text));
          }
          // Persist the complete assistant response
          addMessage(sessionId, 'assistant', fullResponse);
          controller.close();
        } catch (err) {
          console.error('[chat stream]', err);
          controller.error(err);
        }
      },
    });

    return new Response(readableStream, {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Cache-Control': 'no-cache',
        'X-Session-Id': sessionId,
      },
    });
  } catch (error) {
    console.error('[chat]', error);
    const message =
      error instanceof Error && error.message.includes('GEMINI_API_KEY')
        ? 'Gemini API key is not configured. Please set GEMINI_API_KEY in your .env.local file.'
        : 'Failed to get a response from the AI. Please try again.';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
