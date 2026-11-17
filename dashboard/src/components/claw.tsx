/**
 * Pixel-art mechanical claw — the Klaw mark.
 *
 * Drawn on a 16-wide grid from bitmap strings so the favicon, logo and
 * animations all share one drawing. "X" = body (current text colour),
 * "o" = accent, "." = empty. Pure SVG + CSS: no images, crisp at any size.
 */

const OPEN = [
  "......XXXX......",
  ".......XX.......",
  ".....XXXXXX.....",
  "....XXooooXX....",
  ".....XXXXXX.....",
  "....XX.XX.XX....",
  "...XX......XX...",
  "..XX........XX..",
  "..X..........X..",
  "..XX........XX..",
  "...X........X...",
];

const CLOSED = [
  "......XXXX......",
  ".......XX.......",
  ".....XXXXXX.....",
  "....XXooooXX....",
  ".....XXXXXX.....",
  ".....XX..XX.....",
  "....XX....XX....",
  "....X......X....",
  "....XX....XX....",
  ".....XX..XX.....",
  "......X..X......",
];

function Pixels({ rows, accent = "var(--accent)" }: { rows: string[]; accent?: string }) {
  return (
    <>
      {rows.flatMap((row, y) =>
        [...row].map((c, x) =>
          c === "." ? null : (
            <rect key={`${x}-${y}`} x={x} y={y} width="1.02" height="1.02" fill={c === "o" ? accent : "currentColor"} />
          ),
        ),
      )}
    </>
  );
}

/** Static claw (logo, favicon-style mark). */
export function ClawMark({ size = 22, closed = false, className = "" }: { size?: number; closed?: boolean; className?: string }) {
  return (
    <svg
      width={size}
      height={(size * 11) / 16}
      viewBox="0 0 16 11"
      shapeRendering="crispEdges"
      aria-hidden="true"
      className={className}
    >
      <Pixels rows={closed ? CLOSED : OPEN} />
    </svg>
  );
}

/**
 * A claw machine in miniature: the claw drops on its cable, closes on a
 * block (an agent), lifts it, lets go. Respects reduced-motion.
 */
export function ClawMachine({ width = 120, label }: { width?: number; label?: string }) {
  const h = (width * 34) / 16;
  return (
    <div className="claw-machine" style={{ width }} aria-hidden="true">
      <svg viewBox="0 0 16 34" width={width} height={h} shapeRendering="crispEdges">
        {/* rail */}
        <rect x="0" y="0" width="16" height="1" fill="currentColor" opacity="0.35" />
        <g className="claw-rig">
          {/* cable grows with the drop */}
          <rect className="claw-cable" x="7.5" y="1" width="1" height="6" fill="currentColor" opacity="0.6" />
          <g transform="translate(0,6)">
          <g className="claw-head">
            <g className="claw-open">
              <Pixels rows={OPEN.slice(2)} />
            </g>
            <g className="claw-closed">
              <Pixels rows={CLOSED.slice(2)} />
            </g>
            {/* the block it picks up rides with the head once caught */}
            <rect className="claw-prize" x="6" y="8.5" width="4" height="3" fill="var(--accent)" />
          </g>
          </g>
        </g>
        {/* the block waiting on the floor */}
        <rect className="claw-floor-prize" x="6" y="30" width="4" height="3" fill="var(--accent)" />
        <rect x="0" y="33" width="16" height="1" fill="currentColor" opacity="0.35" />
      </svg>
      {label ? <div className="label-mono mt-2 text-center text-faint">{label}</div> : null}
    </div>
  );
}

/** Tiny animated claw for loading states and inline accents. */
export function ClawSpinner({ size = 18 }: { size?: number }) {
  return (
    <span className="claw-snap inline-block" style={{ width: size }} aria-label="Loading" role="status">
      <svg viewBox="0 0 16 11" width={size} height={(size * 11) / 16} shapeRendering="crispEdges">
        <g className="claw-open">
          <Pixels rows={OPEN} />
        </g>
        <g className="claw-closed">
          <Pixels rows={CLOSED} />
        </g>
      </svg>
    </span>
  );
}

export const CLAW_BITMAP = OPEN;
