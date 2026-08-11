"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { ActiveInstructions } from "@/components/instructions";
import {
  Button, Card, EmptyState, ErrorBox, Input, Loading, Pill, Progress, SectionHeader,
} from "@/components/ui";

type Goal = {
  id: string; title: string; metric: string; target_value: number | null;
  unit: string | null; deadline: string | null; constraints: Record<string, unknown>;
  status: string; progress: number; confidence: number | null;
};

export default function GoalsPage() {
  const { data, error, loading, reload } = useApi<Goal[]>("/goals");
  const [text, setText] = useState("");
  const [question, setQuestion] = useState<string | null>(null);
  const [extracted, setExtracted] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [compileError, setCompileError] = useState<string | null>(null);

  async function compile(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setCompileError(null);
    try {
      const result = await api<{ status: string; question: string | null; extracted: Record<string, unknown> | null }>(
        "/goals/compile", { body: { text, prior_extracted: extracted } });
      if (result.status === "needs_clarification") {
        setQuestion(result.question);
        setExtracted(result.extracted);
      } else {
        setQuestion(null);
        setExtracted(null);
        reload();
      }
      setText("");
    } catch (e) {
      setCompileError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <SectionHeader title="Goals" sub="mission objectives" />
      <Card title="Set a new objective">
        <form onSubmit={compile} className="space-y-2">
          <Input
            placeholder='e.g. "€10,000 per month independently before June 2027, under 30 hours/week"'
            value={text} onChange={(e) => setText(e.target.value)}
          />
          {question && <p className="text-sm text-warn">{question}</p>}
          {compileError && <ErrorBox message={compileError} />}
          <Button type="submit" disabled={busy}>
            {busy ? "Compiling…" : question ? "Answer" : "Set goal"}
          </Button>
        </form>
      </Card>

      {!data?.length ? (
        <EmptyState
          label="mission control"
          title="No active mission"
          body="Define what you want your crew to accomplish — one sentence above is enough. Your crew compiles it into a tracked objective."
        />
      ) : (
        <div className="space-y-3">
          {data.map((g) => (
            <Card key={g.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="break-words text-sm font-medium">{g.title}</div>
                  <div className="mt-0.5 font-mono text-[11px] text-ink-faint">
                    {g.metric}
                    {g.target_value != null && ` → ${g.target_value.toLocaleString()} ${g.unit || ""}`}
                    {g.deadline && ` · T-minus ${g.deadline}`}
                  </div>
                  {Object.keys(g.constraints || {}).length > 0 && (
                    <div className="mt-1 break-words text-[11px] text-ink-faint">
                      constraints: {Object.entries(g.constraints).map(([k, v]) => `${k}=${v}`).join(", ")}
                    </div>
                  )}
                </div>
                <Pill color={g.status === "active" ? "green" : "gray"}>{g.status}</Pill>
              </div>
              <div className="mt-3">
                <Progress value={g.progress} />
              </div>
            </Card>
          ))}
        </div>
      )}
      <ActiveInstructions kind="goal_review" title="Goal review instructions"
                          hint="No scheduled goal reviews yet. Ask the ◈ Manager — e.g. “review my goals every Monday morning and flag drift.”" />
    </div>
  );
}
