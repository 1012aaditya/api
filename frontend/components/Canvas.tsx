"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Pan and zoom, hand-rolled.
 *
 * A whiteboard library would bring drawing tools, freeform placement and a
 * few hundred kilobytes, and this board wants none of them: a card's
 * position means its state, so nobody may move one. What is left is a
 * transform on a div, which is small enough to own.
 *
 * Drag anywhere to pan, wheel or pinch to zoom, and the zoom holds the
 * point under the cursor still — anything else feels broken to a hand.
 *
 * Two things are let through rather than captured: a wheel inside anything
 * marked ``data-scroll`` (an opened card's own list scrolls; the board does
 * not move under it) and a click on bare paper, which the board uses to
 * close whatever is open.
 */
export interface Viewport {
  x: number;
  y: number;
  scale: number;
}

const MIN_SCALE = 0.25;
const MAX_SCALE = 1.6;

export function Canvas({
  viewport,
  onViewportChange,
  onBackgroundClick,
  glide = false,
  children,
}: {
  viewport: Viewport;
  onViewportChange: (next: Viewport) => void;
  /** A press and release on bare paper, with no pan in between. */
  onBackgroundClick?: () => void;
  /** Animate the transform, for a move the board made rather than the hand. */
  glide?: boolean;
  children: React.ReactNode;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const dragging = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const moved = useRef(false);
  const [grabbing, setGrabbing] = useState(false);

  const zoomAt = useCallback(
    (clientX: number, clientY: number, factor: number) => {
      const box = frame.current?.getBoundingClientRect();
      if (!box) return;
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, viewport.scale * factor));
      if (scale === viewport.scale) return;
      // Keep the point under the cursor fixed while the scale changes.
      const px = clientX - box.left;
      const py = clientY - box.top;
      const ratio = scale / viewport.scale;
      onViewportChange({
        scale,
        x: px - (px - viewport.x) * ratio,
        y: py - (py - viewport.y) * ratio,
      });
    },
    [viewport, onViewportChange],
  );

  useEffect(() => {
    const node = frame.current;
    if (!node) return;
    // Non-passive, because zooming has to stop the page scrolling with it.
    const onWheel = (event: WheelEvent) => {
      // An opened card's own lists scroll themselves.
      if ((event.target as HTMLElement).closest?.("[data-scroll]")) return;
      if (!event.ctrlKey && !event.metaKey && Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        return; // a horizontal trackpad swipe is a pan, not a zoom
      }
      event.preventDefault();
      if (event.ctrlKey || event.metaKey) {
        zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.08 : 1 / 1.08);
      } else {
        onViewportChange({ ...viewport, x: viewport.x - event.deltaX, y: viewport.y - event.deltaY });
      }
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [viewport, onViewportChange, zoomAt]);

  return (
    <div
      ref={frame}
      className={`relative h-full w-full overflow-hidden ${
        grabbing ? "cursor-grabbing" : "cursor-grab"
      }`}
      onPointerDown={(event) => {
        // Only the background pans; a card must stay clickable.
        if ((event.target as HTMLElement).closest("[data-card]")) return;
        dragging.current = { x: event.clientX, y: event.clientY, vx: viewport.x, vy: viewport.y };
        moved.current = false;
        setGrabbing(true);
        (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
      }}
      onPointerMove={(event) => {
        const start = dragging.current;
        if (!start) return;
        if (Math.abs(event.clientX - start.x) + Math.abs(event.clientY - start.y) > 4) {
          moved.current = true;
        }
        onViewportChange({
          ...viewport,
          x: start.vx + (event.clientX - start.x),
          y: start.vy + (event.clientY - start.y),
        });
      }}
      onPointerUp={() => {
        // A press that went nowhere was a click on the paper, not a pan.
        if (dragging.current && !moved.current) onBackgroundClick?.();
        dragging.current = null;
        setGrabbing(false);
      }}
      onPointerLeave={() => {
        dragging.current = null;
        setGrabbing(false);
      }}
    >
      {/* The paper. Its grid scales with the content so the zoom reads. */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            "radial-gradient(circle, var(--color-axis, #d4d4d8) 1px, transparent 1px)",
          backgroundSize: `${24 * viewport.scale}px ${24 * viewport.scale}px`,
          backgroundPosition: `${viewport.x}px ${viewport.y}px`,
          opacity: 0.4,
        }}
      />
      <div
        className="absolute left-0 top-0 origin-top-left"
        style={{
          transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`,
          transition: glide ? "transform 320ms cubic-bezier(0.22, 1, 0.36, 1)" : undefined,
        }}
      >
        {children}
      </div>
    </div>
  );
}
