import { Info } from 'lucide-react';

/** Shown in every coding editor toolbar, where the Run button used to be.
    Students could previously run their code against sample cases; this build
    evaluates the submitted code instead, so the missing button needs explaining
    rather than looking broken. The "unavailable" sentence is called out in red
    so students don't miss it and waste time looking for a Run button — they
    should dry-run their code on paper instead. */
export const NO_EXECUTION_NOTICE_PREFIX = 'Do a dry run on pen and paper.';
export const NO_EXECUTION_NOTICE_WARNING = 'Code execution is unavailable for this assessment.';
export const NO_EXECUTION_NOTICE =
  `${NO_EXECUTION_NOTICE_PREFIX} ${NO_EXECUTION_NOTICE_WARNING}`;

export default function NoExecutionNotice() {
  return (
    <p className="no-exec-notice">
      <Info size={14} />
      <span>
        {NO_EXECUTION_NOTICE_PREFIX}{' '}
        <span className="no-exec-notice-warning">{NO_EXECUTION_NOTICE_WARNING}</span>
      </span>
    </p>
  );
}
