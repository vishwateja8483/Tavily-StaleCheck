import { useEffect, useState } from 'react';
import type { Job } from './assessment-api';

type StageStatus = 'ready' | 'current' | 'done' | 'stopped' | 'skipped' | 'todo';
const spoken: Record<StageStatus, string> = {
  ready: 'ready to start', current: 'in progress', done: 'complete',
  stopped: 'stopped here', skipped: 'skipped', todo: 'not started',
};
const activeStage: Partial<Record<Job['state'], number>> = { extracting: 0, awaiting_approval: 1, researching: 2 };
const marks: Partial<Record<StageStatus, string>> = { done: '✓', stopped: '!', skipped: '–' };

export default function AssessmentRoute({ job, demo }: { job: Job | null; demo: boolean }) {
  const active = job ? activeStage[job.state] : undefined;
  const [lastActive, setLastActive] = useState(0);
  useEffect(() => { if (active !== undefined) setLastActive(active); }, [active]);

  const mock = demo ? ' (mock)' : '';
  const stages = [
    { title: 'Answer & claims', owner: `You + Bifrost${mock}` },
    { title: 'Approve searches', owner: 'You' },
    { title: 'Research & verify', owner: `Tavily + Bifrost${mock}` },
    { title: 'Report', owner: 'StaleCheck' },
  ];

  const reportReady = job?.artifacts.includes('report.html');
  const interruptedAt = job?.state === 'declined' ? 1
    : job?.state === 'partial' ? (job.approval ? 2 : 0)
    : Math.max(lastActive, job?.approval ? 1 : 0);
  const busy = job?.state === 'extracting' || job?.state === 'researching';

  function stageStatus(index: number): StageStatus {
    if (!job) return index === 0 ? 'ready' : 'todo';
    if (index === 3 && reportReady) return 'done';
    if (job.state === 'completed') return index === 0 || job.approval ? 'done' : 'skipped';
    if (active !== undefined) return index < active ? 'done' : index === active ? 'current' : 'todo';
    return index < interruptedAt ? 'done' : index === interruptedAt ? 'stopped' : 'todo';
  }

  return (
    <ol className="route" role="list" aria-label="Assessment progress" data-busy={busy || undefined}>
      {stages.map((stage, index) => {
        const status = stageStatus(index);
        return (
          <li key={stage.title} data-state={status} aria-current={status === 'ready' || status === 'current' ? 'step' : undefined}>
            <span className="node" aria-hidden="true">{marks[status] ?? index + 1}</span>
            <span className="station">
              <span className="station-title">{stage.title}<span className="sr-only">, {spoken[status]}</span></span>
              <span className="station-owner"><span className="sr-only">Handled by </span>{stage.owner}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
