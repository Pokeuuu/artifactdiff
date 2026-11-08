import { ExternalLink } from 'lucide-react'
import type { ReactNode } from 'react'
import { PageHeader } from '@/components/common'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

const REPO = 'https://github.com/Pokeuuu/artifactdiff'

function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-md border border-line bg-bg p-3 font-mono text-[12.5px] leading-relaxed">
      {children}
    </pre>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-fg-2">{children}</CardContent>
    </Card>
  )
}

export function DocsPage() {
  return (
    <div className="animate-fade-in max-w-3xl">
      <PageHeader
        title="Documentation"
        description="Quick reference. The full guides live in the repository's docs/ folder."
      />
      <div className="space-y-4">
        <Section title="Command line">
          <Code>{`pipx install "artifactdiff[all]"

artifactdiff diff old.parquet new.parquet
artifactdiff diff old.glb new.glb --format html --output report.html
artifactdiff diff data/ data-v2/ --recursive --exclude "*.log"
artifactdiff diff a.csv b.csv -O primary_key=id --fail-on medium
artifactdiff inspect model.safetensors
artifactdiff plugins list`}</Code>
          <p>
            Exit codes: <b>0</b> no differences · <b>1</b> differences · <b>2</b> usage or input error ·{' '}
            <b>3</b> comparison failed.
          </p>
        </Section>
        <Section title="GitHub Action">
          <Code>{`- uses: actions/checkout@v7
  with: { fetch-depth: 0 }
- uses: Pokeuuu/artifactdiff@v0
  with:
    base: \${{ github.event.pull_request.base.sha }}
    head: \${{ github.event.pull_request.head.sha }}
    comment-on-pr: true`}</Code>
          <p>
            Runs on <code className="font-mono">pull_request</code> with{' '}
            <code className="font-mono">contents: read</code> and{' '}
            <code className="font-mono">pull-requests: write</code>. It reads changed files as data and never
            executes code from the pull request.
          </p>
        </Section>
        <Section title="REST API">
          <p>Interactive OpenAPI documentation is served by this server.</p>
          <a
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-accent hover:underline"
          >
            Open API reference <ExternalLink className="size-3.5" />
          </a>
        </Section>
        <Section title="Reading results">
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              <b>Identical</b>: the bytes are equal. <b>Equivalent</b>: the bytes differ but no meaningful
              change was found (e.g. reformatted JSON).
            </li>
            <li>
              Every change carries <b>evidence</b> describing how it was detected. Severity is a documented
              per-plugin heuristic, not a verdict.
            </li>
            <li>Scores such as SSIM or relative L2 difference are measurements, not probabilities.</li>
            <li>
              Each result lists the <b>limitations</b> of the comparison that produced it.
            </li>
          </ul>
        </Section>
        <Section title="Guides">
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {[
              'quickstart',
              'cli',
              'github-action',
              'plugin-development',
              'supported-formats',
              'security',
              'limitations',
              'api',
            ].map((d) => (
              <li key={d}>
                <a
                  href={`${REPO}/blob/main/docs/${d}.md`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-accent hover:underline"
                >
                  {d} <ExternalLink className="size-3" />
                </a>
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </div>
  )
}
