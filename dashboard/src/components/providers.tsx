"use client";

import { usePathname } from "next/navigation";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { getToken } from "@/lib/api";
import { LiveProvider } from "@/lib/live";
import { Shell } from "./shell";

/** Pages that render without a session and without the app shell. */
const PUBLIC_PATHS = ["/login"];

const noSubscribe = () => () => {};

/**
 * Gate the app on a session. Without one, the user is sent to /login, which
 * signs in against the backend and stores a signed, expiring session token.
 * A 401 from any API call clears the token and lands back here.
 */
function SessionGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
  // null during server render / hydration, then whether a token exists.
  const hasToken = useSyncExternalStore(noSubscribe, () => Boolean(getToken()), () => null);

  useEffect(() => {
    if (!isPublic && hasToken === false) window.location.replace("/login");
  }, [isPublic, hasToken]);

  if (isPublic) return <>{children}</>;
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
