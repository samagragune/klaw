"use client";

import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { getToken, isPreview } from "@/lib/api";
import { LiveProvider } from "@/lib/live";
import { Shell } from "./shell";

const noSubscribe = () => () => {};

/**
 * Gate the app on a session. Without one, the user is sent to /login, which
 * signs in against the backend and stores a signed, expiring session token.
 * A 401 from any API call clears the token and lands back here.
 */
function SessionGate({ children }: { children: ReactNode }) {
  // null during server render / hydration, then whether a token exists.
  // Preview mode opens the console with no supervisor behind it.
  const hasToken = useSyncExternalStore(noSubscribe, () => Boolean(getToken()) || isPreview(), () => null);

  useEffect(() => {
    if (hasToken === false) window.location.replace("/login");
  }, [hasToken]);

  if (!hasToken) return null;
  return (
    <LiveProvider>
      <Shell>{children}</Shell>
    </LiveProvider>
  );
}

export function Providers({ children }: { children: ReactNode }) {
  return <SessionGate>{children}</SessionGate>;
}
