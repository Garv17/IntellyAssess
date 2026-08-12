import type { ReactNode } from 'react';
import { useResizablePanes } from '../hooks/useResizablePanes';
import Splitter from './Splitter';

const COLS_KEY = 'coding-workspace-cols';
// 3fr : 5fr : 2fr as a starting point only — dragging is free to move away
// from this ratio entirely (see useResizablePanes); it's never re-enforced
// after the initial mount.
const DEFAULT_COLS = [30, 50, 20];
// Absolute pixel floors, not percentages — a percentage floor shrinks right
// along with a squeezed container and can never actually stop a pane from
// going unusably narrow. These pair with the minmax() grid tracks below: at
// container widths where even the floors don't fit, the grid simply
// overflows and .coding-workspace's own horizontal scroll (see styles.css)
// takes over rather than any pane clipping or shrinking further.
const MIN_COLS_PX = [220, 380, 160];
// No single pane may swallow the whole workspace just because its neighbor
// hasn't hit its own floor yet — keeps the shell recognizably three panes.
const MAX_COLS_PCT = [45, 70, 35];
const DIVIDER_PX = 10;

/**
 * The IDE-style shell for a coding question: three independently-scrolling
 * panes — problem/testcases, editor+results, question navigator — in one
 * draggable-ratio row that fills the viewport height below the exam header.
 * Callers supply each pane's content; this component owns only the grid and
 * the resize state, so SQL and generic coding questions can plug in totally
 * different pane bodies without duplicating the shell.
 */
export default function CodingWorkspace({
  problemHead,
  problemBody,
  problemFoot,
  editorPane,
  navigatorPane,
}: {
  problemHead: ReactNode;
  problemBody: ReactNode;
  problemFoot: ReactNode;
  editorPane: ReactNode;
  navigatorPane: ReactNode;
}) {
  const { containerRef, ratios, resize } = useResizablePanes(
    COLS_KEY,
    DEFAULT_COLS,
    MIN_COLS_PX,
    'width',
    MAX_COLS_PCT,
  );

  return (
    <div
      className="coding-workspace"
      ref={containerRef}
      style={{
        gridTemplateColumns: `minmax(${MIN_COLS_PX[0]}px, ${ratios[0]}fr) ${DIVIDER_PX}px minmax(${MIN_COLS_PX[1]}px, ${ratios[1]}fr) ${DIVIDER_PX}px minmax(${MIN_COLS_PX[2]}px, ${ratios[2]}fr)`,
      }}
    >
      <section className="cw-pane cw-problem">
        <div className="cw-pane-head">{problemHead}</div>
        <div className="cw-pane-body">{problemBody}</div>
        <div className="cw-pane-foot">{problemFoot}</div>
      </section>

      <Splitter direction="horizontal" onResize={(delta) => resize(0, delta)} />

      <section className="cw-pane cw-editor">{editorPane}</section>

      <Splitter direction="horizontal" onResize={(delta) => resize(1, delta)} />

      <aside className="cw-pane cw-nav">{navigatorPane}</aside>
    </div>
  );
}
