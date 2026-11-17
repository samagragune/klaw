import { Providers } from "@/components/providers";

/** Everything behind sign-in: the operator console. */
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <Providers>{children}</Providers>;
}
