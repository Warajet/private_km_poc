import { NextResponse } from 'next/server';
import { getAuthFromRequest } from '@/lib/auth';
import { getSessionsByUser, createSession } from '@/lib/db';

export async function GET(request: Request) {
  const auth = await getAuthFromRequest(request);
  if (!auth) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const sessions = getSessionsByUser(auth.userId);
  return NextResponse.json(sessions);
}

export async function POST(request: Request) {
  const auth = await getAuthFromRequest(request);
  if (!auth) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const session = createSession(auth.userId);
  return NextResponse.json(session, { status: 201 });
}
