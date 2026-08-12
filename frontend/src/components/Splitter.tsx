import { useCallback, useRef } from 'react';

interface Props {
  direction: 'horizontal' | 'vertical';
  onResize: (deltaPx: number) => void;
}

/** A thin drag handle between two panes. Reports the raw pointer delta on every
    move and lets the caller decide how to clamp/apply it — this component has no
    opinion on min/max sizes. */
export default function Splitter({ direction, onResize }: Props) {
  const lastPos = useRef(0);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      lastPos.current = direction === 'horizontal' ? e.clientX : e.clientY;

      const onMove = (ev: PointerEvent) => {
        const pos = direction === 'horizontal' ? ev.clientX : ev.clientY;
        onResize(pos - lastPos.current);
        lastPos.current = pos;
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    },
    [direction, onResize],
  );

  return (
    <div
      className={`splitter splitter-${direction}`}
      onPointerDown={onPointerDown}
      role="separator"
      aria-orientation={direction === 'horizontal' ? 'vertical' : 'horizontal'}
    />
  );
}
