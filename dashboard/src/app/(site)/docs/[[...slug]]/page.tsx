import Link from "next/link";
import { notFound } from "next/navigation";
import { DOCS, findDoc } from "@/components/docs-content";

export function generateStaticParams() {
  return DOCS.map((d) => ({ slug: d.slug ? d.slug.split("/") : [] }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug?: string[] }> }) {
  const doc = findDoc((await params).slug);
  return { title: doc ? `${doc.title} — Reliability docs` : "Reliability docs" };
}

export default async function DocPage({ params }: { params: Promise<{ slug?: string[] }> }) {
  const doc = findDoc((await params).slug);
  if (!doc) notFound();
  const index = DOCS.indexOf(doc);
  const prev = DOCS[index - 1];
  const next = DOCS[index + 1];
  const groups = Array.from(new Set(DOCS.map((d) => d.group)));

  return (
    <div className="mx-auto grid max-w-[1280px] md:grid-cols-[250px_1fr]">
      <aside className="border-b border-line px-5 py-8 md:sticky md:top-16 md:h-[calc(100vh-4rem)] md:overflow-y-auto md:border-r md:border-b-0 md:px-8">
        <div className="bracket mb-6">Docs</div>
        {groups.map((group) => (
          <div key={group} className="mb-7">
            <div className="label-mono mb-3 text-faint">{group}</div>
            <ul className="space-y-1">
              {DOCS.filter((d) => d.group === group).map((d) => {
                const active = d.slug === doc.slug;
                return (
                  <li key={d.slug}>
                    <Link
                      href={d.slug ? `/docs/${d.slug}` : "/docs"}
                      className={`block border-l py-1 pl-3 text-sm ${
                        active ? "border-[var(--accent)] text-ink" : "border-line text-muted hover:text-ink"
                      }`}
                    >
                      {d.title}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </aside>
      <article className="min-w-0 px-5 py-12 md:px-14 md:py-16">
        <div className="label-mono text-faint">{doc.group}</div>
        <h1 className="display display-bold mt-3 text-[48px] md:text-[64px]">{doc.title}</h1>
        <p className="mt-4 max-w-2xl text-[17px] text-muted">{doc.summary}</p>
        <div className="mt-8 max-w-3xl border-t border-line pt-4">{doc.body}</div>
        <nav className="mt-16 grid max-w-3xl gap-3 border-t border-line pt-6 sm:grid-cols-2">
          {prev ? (
            <Link href={prev.slug ? `/docs/${prev.slug}` : "/docs"} className="border border-line p-4 hover:border-line-strong">
              <div className="label-mono text-faint">← Previous</div>
              <div className="mt-1 text-ink">{prev.title}</div>
            </Link>
          ) : (
            <span />
          )}
          {next ? (
            <Link href={`/docs/${next.slug}`} className="border border-line p-4 text-right hover:border-line-strong">
              <div className="label-mono text-faint">Next →</div>
              <div className="mt-1 text-ink">{next.title}</div>
            </Link>
          ) : null}
        </nav>
      </article>
    </div>
  );
}
