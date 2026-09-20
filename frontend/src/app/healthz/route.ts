/**
 * Liveness for the deployed web server.
 *
 * Deliberately separate from the dashboard: `/` renders panels whose data
 * comes from the API, so using it as a health check would make the web service
 * look unhealthy whenever the *backend* is down. This answers only the
 * question the platform is asking — is this server up and serving?
 */

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({ status: "ok", service: "web" });
}
