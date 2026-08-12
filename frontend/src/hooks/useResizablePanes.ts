import { useCallback, useEffect, useRef, useState } from 'react';

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function loadRatios(key: string, defaults: number[]): number[] {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length === defaults.length && parsed.every((n) => typeof n === 'number')) {
      return parsed as number[];
    }
  } catch {
    // Storage can be unavailable or hold a stale shape from an older layout —
    // either way, falling back to the default split is harmless.
  }
  return defaults;
}

/**
 * Percentage-of-container pane sizing — not fixed pixels — so a saved split
 * still looks the same fraction of the screen at a different resolution or
 * browser zoom instead of drifting toward one pane starving the others.
 *
 * `ratios` always sums to 100. Each divider only trades share between its two
 * immediate neighbors (index `i` and `i+1`); every other pane is untouched,
 * which is what keeps the sum exactly 100 with no extra normalization step.
 *
 * `minPx` is an absolute floor per pane, not a percentage — a percentage
 * floor shrinks right along with a squeezed container, so it can never
 * actually stop a pane from becoming unusably narrow. Converting each pane's
 * px floor to "percent of the container's *current* width" on every resize
 * means the drag clamp always reflects a real, fixed minimum size regardless
 * of zoom or window width. The caller is expected to pair this with CSS
 * `minmax(floorPx, Nfr)` grid tracks (see CodingWorkspace.tsx) as the true
 * layout-level guarantee — if the floors themselves don't fit the container,
 * the grid overflows and the workspace scrolls instead of any pane clipping.
 * `maxPct` is an optional flat ceiling (percent of the whole container) per
 * pane, so no single pane can swallow the layout just because its neighbor
 * hasn't hit its own floor yet.
 */
export function useResizablePanes(
  key: string,
  defaults: number[],
  minPx: number[],
  axis: 'width' | 'height',
  maxPct?: number[],
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [ratios, setRatios] = useState<number[]>(() => loadRatios(key, defaults));

  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(ratios));
    } catch {
      // Resizing still works for the session even if it can't persist.
    }
  }, [key, ratios]);

  const resize = useCallback(
    (dividerIndex: number, deltaPx: number) => {
      const el = containerRef.current;
      if (!el) return;
      const total = axis === 'width' ? el.clientWidth : el.clientHeight;
      if (!total) return;
      const deltaPct = (deltaPx / total) * 100;
      setRatios((prev) => {
        const a = dividerIndex;
        const b = dividerIndex + 1;
        if (a < 0 || b >= prev.length) return prev;
        const pairSum = prev[a] + prev[b];
        const minAPct = Math.min((minPx[a] / total) * 100, pairSum);
        const minBPct = Math.min((minPx[b] / total) * 100, pairSum);
        const capAPct = maxPct?.[a] ?? 100;
        const highA = Math.max(minAPct, Math.min(pairSum - minBPct, capAPct));
        const nextA = clamp(prev[a] + deltaPct, minAPct, highA);
        const nextB = pairSum - nextA;
        const next = [...prev];
        next[a] = nextA;
        next[b] = nextB;
        return next;
      });
    },
    [axis, minPx, maxPct],
  );

  return { containerRef, ratios, resize };
}

// Must match .splitter-vertical { height } in styles.css — there's exactly one
// vertical splitter between the editor and the results panel, and its height
// is real, fixed chrome that eats into the space available to both panes.
const SPLITTER_PX = 13;

type VerticalSplitOptions = {
  /** Flat floor (px) below which this pane refuses to shrink, whether that's
      from a drag or from squeezed available space. Below this the pane just
      stays put and .cw-editor-inner's own overflow-y:auto (see styles.css)
      scrolls the column instead of clipping — this never scales down further,
      so it's the one number to tune for "still usable" on each pane. */
  editorFloorPx: number;
  resultsFloorPx: number;
  /** Optional ceiling (percent of the editor+results space) so a drag can't
      let one pane swallow effectively all of it just because the other
      hasn't hit its floor yet. Defaults to no cap. */
  editorMaxPct?: number;
  resultsMaxPct?: number;
};

function loadShare(key: string, fallback: number): number {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? Number(JSON.parse(raw)) : NaN;
    return Number.isFinite(parsed) ? clamp(parsed, 0, 100) : fallback;
  } catch {
    return fallback;
  }
}

/**
 * The editor/results vertical split used inside a coding question's editor
 * pane. Unlike useResizablePanes above, the two panes' minimum sizes are not
 * a percent of the whole container — a percentage can't know how tall the
 * toolbar or the tab strip actually rendered (both wrap to two lines at
 * narrow widths), so a hard-coded percentage floor can end up larger than
 * what's actually left over and force the column to overflow.
 *
 * Instead this measures the container and the real chrome elements (toolbar,
 * tab strip) via ResizeObserver to find how much height is genuinely left
 * for the two panes (`available`), and applies each pane's floor as a flat
 * px minimum against *that* — both for the CSS safety net (min-height, so a
 * squeeze can never crush a pane below it) and as the drag clamp (so the
 * user can freely trade space between the two panes anywhere between their
 * floors, not just within some narrower band). Earlier this scaled the
 * minimum up with available space to "reserve" comfortable room, but that
 * scaled value was also what bounded dragging — with lots of room the
 * reserved minimum ballooned and made most of that room undraggable. A flat
 * floor doesn't have that problem: it only ever matters when space is
 * actually tight.
 *
 * If even the two floors plus chrome don't fit the container, both panes
 * simply stay at their floor and .cw-editor-inner's own overflow-y:auto (see
 * styles.css) lets that one column scroll as a whole instead of clipping —
 * Monaco itself never becomes the scrolling element, only its position
 * within the column does.
 */
export function useVerticalSplit(key: string, defaultEditorShare: number, opts: VerticalSplitOptions) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chromeTopRef = useRef<HTMLDivElement | null>(null);
  const chromeBottomRef = useRef<HTMLDivElement | null>(null);

  const [share, setShare] = useState<number>(() => loadShare(key, defaultEditorShare));
  const [available, setAvailable] = useState(0);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const recompute = () => {
      const chromeH =
        (chromeTopRef.current?.getBoundingClientRect().height ?? 0) +
        (chromeBottomRef.current?.getBoundingClientRect().height ?? 0) +
        SPLITTER_PX;
      setAvailable(Math.max(0, container.clientHeight - chromeH));
    };
    recompute();
    const ro = new ResizeObserver(recompute);
    ro.observe(container);
    if (chromeTopRef.current) ro.observe(chromeTopRef.current);
    if (chromeBottomRef.current) ro.observe(chromeBottomRef.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(share));
    } catch {
      // Resizing still works for the session even if it can't persist.
    }
  }, [key, share]);

  const resize = useCallback(
    (deltaPx: number) => {
      if (!available) return;
      setShare((prev) => {
        const prevEditorPx = (prev / 100) * available;
        // Editor can't go so low that results would exceed its own cap, and can't
        // go so high that results would drop below its floor — floors/caps on
        // *either* pane both end up bounding editor's valid range from one side.
        const lowFromEditorFloor = opts.editorFloorPx;
        const lowFromResultsCap = available - ((opts.resultsMaxPct ?? 100) / 100) * available;
        const lowEditorPx = Math.max(lowFromEditorFloor, lowFromResultsCap);

        const highFromResultsFloor = available - opts.resultsFloorPx;
        const highFromEditorCap = ((opts.editorMaxPct ?? 100) / 100) * available;
        const highEditorPx = Math.max(lowEditorPx, Math.min(highFromResultsFloor, highFromEditorCap));

        const nextEditorPx = clamp(prevEditorPx + deltaPx, lowEditorPx, highEditorPx);
        return (nextEditorPx / available) * 100;
      });
    },
    [available, opts.editorFloorPx, opts.resultsFloorPx, opts.editorMaxPct, opts.resultsMaxPct],
  );

  return {
    containerRef,
    chromeTopRef,
    chromeBottomRef,
    ratios: [share, 100 - share],
    mins: [opts.editorFloorPx, opts.resultsFloorPx],
    resize,
  };
}
