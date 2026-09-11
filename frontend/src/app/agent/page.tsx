import { Suspense } from "react";

import { AgentConsole } from "./AgentConsole";
import { LoadingState } from "@/components/ui/States";

export const metadata = { title: "Agent — ULUGBEK AI" };

export default function AgentPage() {
  return (
    <Suspense fallback={<LoadingState rows={5} label="Loading the agent console" />}>
      <AgentConsole />
    </Suspense>
  );
}
