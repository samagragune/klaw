import { SiteFooter, SiteNav } from "@/components/site";

export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-bg">
      <SiteNav />
      <main>{children}</main>
      <SiteFooter />
    </div>
  );
}
