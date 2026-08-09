import { useEffect, useRef } from "react";

/** Live audio waveform stand-in — the signature element for audio tasks. */
export function Waveform({
  percent,
  active,
}: {
  percent: number;
  active: boolean;
}) {
  const bars = 40;
  const ref = useRef<HTMLDivElement>(null);
  const frame = useRef(0);

  useEffect(() => {
    if (!active) return;
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    let raf = 0;
    const tick = () => {
      frame.current += 1;
      const el = ref.current;
      if (el) {
        const children = el.children;
        for (let i = 0; i < children.length; i += 1) {
          const t = frame.current / 9 + i / 3;
          const h =
            22 +
            Math.abs(Math.sin(t) * 42) +
            Math.abs(Math.sin(t * 0.37 + i) * 30);
          (children[i] as HTMLElement).style.height = `${Math.min(100, h)}%`;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active]);

  return (
    <div className="relative h-9 w-full overflow-hidden rounded-md bg-panel-raised/70">
      <div
        ref={ref}
        className="absolute inset-0 flex items-center justify-between gap-[2px] px-1.5"
        aria-hidden
      >
        {Array.from({ length: bars }, (_, i) => (
          <span
            key={i}
            className="w-full rounded-full bg-signal transition-[height] duration-150"
            style={{
              height: `${20 + ((i * 7) % 60)}%`,
              opacity: active ? 1 : 0.35,
            }}
          />
        ))}
      </div>
      <div
        className="absolute inset-y-0 right-0 bg-background/70 backdrop-grayscale transition-[width] duration-500 ease-out"
        style={{ width: `${100 - Math.min(100, percent)}%` }}
      />
    </div>
  );
}
