import { useEffect, useState } from 'react';
import type { Job } from './assessment-api';

type JobState = Job['state'];
type Station = { title: string; owner: string };

const stationOf: Partial<Record<JobState, number>> = { extracting: 0, awaiting_approval: 1, declined: 1, researching: 2, partial: 3 };
const stopped: ReadonlySet<JobState> = new Set(['declined', 'cancel_requested', 'cancelled', 'failed', 'expired', 'partial']);
const spoken = { done: 'done', current: 'in progress', stopped: 'stopped here', todo: 'not started' } as const;

function stations(demo: boolean): Station[] {
  const mock = demo ? ' (mock)' : '';
  return [
    { title: 'Answer & claims', owner: `You + Bifrost${mock}` },
    { title: 'Approve searches', owner: 'You' },
    { title: 'Research', owner: `Tavily + Bifrost${mock}` },
    { title: 'Verify & report', owner: 'StaleCheck' },
  ];
}

export default function AssessmentRoute({ job, demo }: { job: Job | null; demo: boolean }) {
  const known = job ? stationOf[job.state] : undefined;
  const [lastKnown, setLastKnown] = useState(0);
  useEffect(() => { if (known !== undefined) setLastKnown(known); }, [known]);
  const finished = job?.state === 'completed';
  const halted = !!job && stopped.has(job.state);
  const at = !job ? 0 : finished ? 3 : known ?? lastKnown;
  const busy = job?.state === 'extracting' || job?.state === 'researching';

  return (
    <ol className="route" role="list" aria-label="Assessment progress" data-busy={busy || undefined}>
      {stations(demo).map((station, index) => {
        const state = index < at ? 'done' : index > at ? 'todo' : finished ? 'done' : halted ? 'stopped' : 'current';
        return (
          <li key={station.title} data-state={state} aria-current={index === at ? 'step' : undefined}>
            <span className="node" aria-hidden="true">{state === 'done' ? '✓' : index + 1}</span>
            <span className="station">
              <span className="station-title">{station.title}<span className="sr-only">, {spoken[state]}</span></span>
              <span className="station-owner"><span className="sr-only">Handled by </span>{station.owner}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
