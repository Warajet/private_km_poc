import { NextResponse } from 'next/server';
import { getAuthFromRequest } from '@/lib/auth';
import { getSessionById, deleteSession } from '@/lib/db';

export async function GET(request: Request, { params }: { params: { id: string } }) {
  const auth = await getAuthFromRequest(request);
  if (!auth) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const session = await getSessionById(params.id, auth.userId);
  if (!session) {
    return NextResponse.json({ error: 'Session not found' }, { status: 404 });
  }

  return NextResponse.json(session);
}

export async function DELETE(request: Request, { params }: { params: { id: string } }) {
  const auth = await getAuthFromRequest(request);
  if (!auth) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const session = await getSessionById(params.id, auth.userId);
  if (!session) {
    return NextResponse.json({ error: 'Session not found' }, { status: 404 });
  }

  await deleteSession(params.id, auth.userId);
  return NextResponse.json({ success: true });
}
