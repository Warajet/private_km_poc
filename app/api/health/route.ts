import { NextResponse } from 'next/server';

/**
 * GET /api/health
 *
 * Unauthenticated liveness / readiness probe used by Kubernetes.
 * Returns 200 as long as the Next.js process is running.
 */
export async function GET() {
  return NextResponse.json({ status: 'ok', timestamp: Date.now() });
}
