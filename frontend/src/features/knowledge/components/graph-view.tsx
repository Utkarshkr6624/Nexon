import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Crosshair, Maximize2, Minus, Plus } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { LoadingState } from '@/components/feedback/loading-state'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { useKnowledgeGraph } from '@/features/knowledge/hooks'
import { toApiError } from '@/services/errors'
import { cn } from '@/lib/utils'
import { KNOWLEDGE_ENTITY_META, KNOWLEDGE_ENTITY_TYPES } from '@/types/knowledge'
import type { KnowledgeEntityType, KnowledgeGraphNode, UUIDString } from '@/types/knowledge'

import { EmptyKnowledge } from './empty-knowledge'

/**
 * The client-side node cap, **below** the server's 500.
 *
 * The repulsion below is O(n²) per tick — every node against every other — so
 * 500 nodes is 250k pair computations per frame. That is not the interesting
 * cost, though: the layout stops being *readable* long before it stops being
 * fast. A few hundred unlabelled dots is a hairball, and the spec names exactly
 * that as the failure mode — a graph that looks impressive but becomes unusable.
 *
 * A Barnes–Hut quadtree would make the force cheap, and would make the picture
 * exactly as unreadable, faster. So the honest move is the one taken here: draw
 * a bounded graph, label what fits, and **say** when the picture is a subset.
 * The cap is a product decision, not a performance limit, and the UI reports it
 * rather than quietly drawing 300 of 900 nodes as though they were all of it.
 */
const MAX_RENDERED_NODES = 200

/** Below this zoom, labels are dropped: 200 overlapping strings is not a label
 *  set, it is a smudge. Zoom in and they come back. */
const LABEL_ZOOM_THRESHOLD = 0.7
const LABEL_COUNT_THRESHOLD = 80

const WIDTH = 900
const HEIGHT = 620
const IDEAL_EDGE_LENGTH = 70
const MAX_TICKS = 400
/** Redraws per second during the simulation. 60fps React re-renders of a few
 *  hundred SVG nodes buys nothing the eye can follow at this scale. */
const FRAME_INTERVAL_MS = 80

interface SimNode {
  id: UUIDString
  type: KnowledgeEntityType
  label: string
  x: number
  y: number
  vx: number
  vy: number
}

interface SimEdge {
  source: number
  target: number
}

interface Layout {
  key: string
  nodes: SimNode[]
  edges: SimEdge[]
}

interface Viewport {
  scale: number
  tx: number
  ty: number
}

const MIN_SCALE = 0.3
const MAX_SCALE = 3

/** Deterministic seed on a golden-angle spiral, so the same graph always starts
 *  from the same shape and the layout does not jump when a query refetches. */
function seedNodes(nodes: KnowledgeGraphNode[]): SimNode[] {
  const golden = Math.PI * (3 - Math.sqrt(5))
  const radius = 12 * Math.sqrt(nodes.length)
  return nodes.map((node, index) => {
    const angle = index * golden
    return {
      id: node.id,
      type: node.type,
      label: node.label,
      x: WIDTH / 2 + radius * Math.cos(angle),
      y: HEIGHT / 2 + radius * Math.sin(angle),
      vx: 0,
      vy: 0,
    }
  })
}

const TYPE_FILL: Record<KnowledgeEntityType, string> = {
  note: 'fill-primary',
  concept: 'fill-warning',
  resource: 'fill-muted-foreground',
}

export interface GraphViewProps {
  /** Server-side node cap to ask for. `?limit=501` is a 422. */
  limit?: number
  entityType?: KnowledgeEntityType
  onEntityTypeChange?: (type: KnowledgeEntityType | undefined) => void
  onSelectNode?: (node: KnowledgeGraphNode) => void
  className?: string
}

/**
 * A force-directed graph, hand-rolled in SVG.
 *
 * No graph library is installed and none may be added, so the layout is the
 * integrator below: repulsion between every pair, spring attraction along every
 * edge, a weak pull to the centre, velocity damping and an alpha that cools so
 * the layout settles and stops burning frames. That is the whole algorithm —
 * roughly it is what d3-force does, minus the quadtree, the cluster forces and
 * the collision pass, none of which this view needs to stay readable.
 */
export function GraphView({
  limit = MAX_RENDERED_NODES,
  entityType,
  onEntityTypeChange,
  onSelectNode,
  className,
}: GraphViewProps) {
  const graph = useKnowledgeGraph({ limit, entity_type: entityType })
  const [selected, setSelected] = useState<UUIDString | null>(null)
  const [viewport, setViewport] = useState<Viewport>({ scale: 1, tx: 0, ty: 0 })

  const nodesRef = useRef<SimNode[]>([])
  const alphaRef = useRef(1)
  /**
   * The integrator mutates `nodesRef` in place; what is *drawn* is this state
   * copy, published on a throttle from the animation frame. A ref read during
   * render would not re-render on change anyway — this is the render-facing
   * snapshot, and the ref is the simulation's private workspace.
   */
  const [painted, setPainted] = useState<Layout | null>(null)
  const rafRef = useRef<number | null>(null)
  const panRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null)

  /** Nodes the picture draws, after the client cap. */
  const visible = useMemo(() => {
    const all = graph.data?.nodes ?? []
    return { nodes: all.slice(0, MAX_RENDERED_NODES), dropped: Math.max(0, all.length - MAX_RENDERED_NODES) }
  }, [graph.data])

  /**
   * The seeded layout for the current graph, and the edge index pairs. Pure, so
   * the effect below can re-seed without writing state during render: the
   * simulation publishes a *painted* snapshot from its animation frame, and
   * until that arrives (or after the graph changes) the seeded layout is what
   * renders.
   */
  const seeded = useMemo(() => seedNodes(visible.nodes), [visible.nodes])
  const seedEdges = useMemo<SimEdge[]>(() => {
    const index = new Map<UUIDString, number>()
    seeded.forEach((node, i) => index.set(node.id, i))
    // An edge to a node the cap dropped is dropped with it, rather than drawn
    // to nothing.
    return (graph.data?.edges ?? [])
      .map((edge) => ({
        source: index.get(edge.source) ?? -1,
        target: index.get(edge.target) ?? -1,
      }))
      .filter((edge) => edge.source >= 0 && edge.target >= 0)
  }, [seeded, graph.data])

  const graphKey = `${seeded.length}:${seeded[0]?.id ?? ''}:${seedEdges.length}`
  const layout: Layout = painted && painted.key === graphKey ? painted : { key: graphKey, nodes: seeded, edges: seedEdges }

  /** Indices of everything one hop from the selection; the rest fades back. */
  const neighbours = useMemo(() => {
    const set = new Set<UUIDString>()
    if (!selected || !graph.data) return set
    for (const edge of graph.data.edges) {
      if (edge.source === selected) set.add(edge.target)
      if (edge.target === selected) set.add(edge.source)
    }
    return set
  }, [selected, graph.data])

  // Seed the simulation once per graph. Re-running on every render would freeze
  // the layout in place, which is the classic way a hand-rolled graph goes dead.
  useEffect(() => {
    const nodes = seeded
    const edges = seedEdges
    nodesRef.current = nodes
    alphaRef.current = 1

    let lastFrame = 0
    let ticks = 0

    const step = (timestamp: number): void => {
      const nodes = nodesRef.current
      const alpha = alphaRef.current

      // O(n²) repulsion. Every node pushes every other apart; this is the cost
      // the cap above exists to keep bounded.
      for (let i = 0; i < nodes.length; i += 1) {
        for (let j = i + 1; j < nodes.length; j += 1) {
          const a = nodes[i]!
          const b = nodes[j]!
          let dx = a.x - b.x
          let dy = a.y - b.y
          let distanceSq = dx * dx + dy * dy
          if (distanceSq < 1) {
            // Coincident nodes: nudge them apart deterministically rather than
            // dividing by zero, or the pair sticks together forever.
            dx = (i - j) * 0.5 + 0.5
            dy = 0.5
            distanceSq = dx * dx + dy * dy
          }
          const distance = Math.sqrt(distanceSq)
          const force = ((IDEAL_EDGE_LENGTH * IDEAL_EDGE_LENGTH) / distance) * alpha
          const fx = (dx / distance) * force
          const fy = (dy / distance) * force
          a.vx += fx
          a.vy += fy
          b.vx -= fx
          b.vy -= fy
        }
      }

      // Springs along the edges: pull towards the ideal edge length.
      for (const edge of edges) {
        const a = nodes[edge.source]!
        const b = nodes[edge.target]!
        const dx = b.x - a.x
        const dy = b.y - a.y
        const distance = Math.max(1, Math.hypot(dx, dy))
        const force = ((distance * distance) / IDEAL_EDGE_LENGTH) * alpha * 0.08
        const fx = (dx / distance) * force
        const fy = (dy / distance) * force
        a.vx += fx
        a.vy += fy
        b.vx -= fx
        b.vy -= fy
      }

      // Integrate with damping, then cool. Cooling is what stops this: an
      // undamped simulation jitters for ever and a React component re-rendering
      // at 12fps is not somewhere to burn a battery.
      for (const node of nodes) {
        node.vx = (node.vx + (WIDTH / 2 - node.x) * 0.006 * alpha) * 0.82
        node.vy = (node.vy + (HEIGHT / 2 - node.y) * 0.006 * alpha) * 0.82
        node.x += Math.max(-12, Math.min(12, node.vx))
        node.y += Math.max(-12, Math.min(12, node.vy))
        node.x = Math.max(30, Math.min(WIDTH - 30, node.x))
        node.y = Math.max(24, Math.min(HEIGHT - 24, node.y))
      }

      alphaRef.current = alpha * 0.985
      ticks += 1

      if (timestamp - lastFrame >= FRAME_INTERVAL_MS) {
        lastFrame = timestamp
        setPainted({ key: graphKey, nodes: [...nodes], edges })
      }

      if (alphaRef.current > 0.005 && ticks < MAX_TICKS) {
        rafRef.current = requestAnimationFrame(step)
      } else {
        // One final paint so the settled layout is what is on screen.
        setPainted({ key: graphKey, nodes: [...nodes], edges })
        rafRef.current = null
      }
    }

    rafRef.current = requestAnimationFrame(step)
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [seeded, seedEdges, graphKey])

  const zoomBy = useCallback((factor: number) => {
    setViewport((current) => {
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, current.scale * factor))
      return { ...current, scale }
    })
  }, [])

  const resetView = useCallback(() => {
    setViewport({ scale: 1, tx: 0, ty: 0 })
  }, [])

  if (graph.isPending) {
    return <LoadingState label="Laying out the graph" description="Every node against every other." />
  }

  if (graph.error) {
    return (
      <ErrorState
        error={toApiError(graph.error)}
        onRetry={() => void graph.refetch()}
        title="The graph could not be loaded"
      />
    )
  }

  const { nodes, edges } = layout
  const total = graph.data?.nodes.length ?? 0

  if (total === 0) {
    return <EmptyKnowledge kind="graph" />
  }

  const selectedIndex = selected ? nodes.findIndex((node) => node.id === selected) : -1
  const showLabels = nodes.length <= LABEL_COUNT_THRESHOLD || viewport.scale >= LABEL_ZOOM_THRESHOLD
  const capped = graph.data?.truncated === true || visible.dropped > 0

  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={entityType ?? ''}
          aria-label="Filter the graph by kind"
          onChange={(event) =>
            onEntityTypeChange?.((event.target.value || undefined) as KnowledgeEntityType | undefined)
          }
          className="w-44"
        >
          <option value="">All kinds</option>
          {KNOWLEDGE_ENTITY_TYPES.map((kind) => (
            <option key={kind} value={kind}>
              {KNOWLEDGE_ENTITY_META[kind].label}
            </option>
          ))}
        </Select>

        <span className="text-xs text-muted-foreground">
          {nodes.length} node{nodes.length === 1 ? '' : 's'} · {graph.data?.edges.length ?? 0} link
          {(graph.data?.edges.length ?? 0) === 1 ? '' : 's'}
        </span>

        {capped && (
          <span className="rounded-md border border-warning/40 bg-warning/10 px-2 py-0.5 text-xs text-warning">
            Showing {nodes.length} of {total} — a larger graph stops being readable.
            {visible.dropped > 0 && ` ${visible.dropped} not drawn.`}
          </span>
        )}

        <div className="ml-auto flex items-center gap-1">
          <Button type="button" variant="outline" size="icon" className="size-8" onClick={() => zoomBy(0.8)}>
            <Minus aria-hidden="true" className="size-4" />
            <span className="sr-only">Zoom out</span>
          </Button>
          <Button type="button" variant="outline" size="icon" className="size-8" onClick={() => zoomBy(1.25)}>
            <Plus aria-hidden="true" className="size-4" />
            <span className="sr-only">Zoom in</span>
          </Button>
          <Button type="button" variant="outline" size="icon" className="size-8" onClick={resetView}>
            <Maximize2 aria-hidden="true" className="size-4" />
            <span className="sr-only">Reset the view</span>
          </Button>
        </div>
      </div>

      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-[min(70vh,620px)] w-full touch-none rounded-lg border border-border bg-card"
        role="application"
        aria-label="Knowledge graph"
        onWheel={(event) => {
          event.preventDefault()
          zoomBy(event.deltaY < 0 ? 1.1 : 0.9)
        }}
        onPointerDown={(event) => {
          panRef.current = { x: event.clientX, y: event.clientY, tx: viewport.tx, ty: viewport.ty }
          event.currentTarget.setPointerCapture(event.pointerId)
        }}
        onPointerMove={(event) => {
          const pan = panRef.current
          if (!pan) return
          setViewport((current) => ({
            ...current,
            tx: pan.tx + (event.clientX - pan.x),
            ty: pan.ty + (event.clientY - pan.y),
          }))
        }}
        onPointerUp={() => {
          panRef.current = null
        }}
        onClick={() => setSelected(null)}
      >
        <g transform={`translate(${viewport.tx} ${viewport.ty}) scale(${viewport.scale})`}>
          <g className="stroke-border" strokeWidth={1}>
            {edges.map((edge, index) => {
              const from = nodes[edge.source]
              const to = nodes[edge.target]
              if (!from || !to) return null
              const active =
                selectedIndex >= 0 &&
                (edge.source === selectedIndex || edge.target === selectedIndex)
              return (
                <line
                  key={`${edge.source}-${edge.target}-${index}`}
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  className={active ? 'stroke-primary' : 'stroke-border'}
                  opacity={selected === null ? 0.6 : active ? 1 : 0.15}
                />
              )
            })}
          </g>

          <g>
            {nodes.map((node) => {
              const isSelected = node.id === selected
              const isNeighbour = neighbours.has(node.id)
              const dimmed = selected !== null && !isSelected && !isNeighbour
              const labelled = showLabels || isSelected || isNeighbour
              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x} ${node.y})`}
                  className="cursor-pointer"
                  opacity={dimmed ? 0.3 : 1}
                  onClick={(event) => {
                    event.stopPropagation()
                    setSelected(node.id)
                    onSelectNode?.({ id: node.id, type: node.type, label: node.label })
                  }}
                >
                  <circle
                    r={isSelected ? 9 : 6}
                    className={cn(TYPE_FILL[node.type], isSelected && 'stroke-primary')}
                    strokeWidth={isSelected ? 2 : 0}
                    fillOpacity={isSelected || isNeighbour ? 1 : 0.75}
                  />
                  {labelled && (
                    <text
                      y={-11}
                      textAnchor="middle"
                      className="fill-foreground text-[11px]"
                      style={{ fontSize: 11 }}
                    >
                      {node.label.length > 28 ? `${node.label.slice(0, 27)}…` : node.label}
                    </text>
                  )}
                  <title>{`${KNOWLEDGE_ENTITY_META[node.type].label}: ${node.label}`}</title>
                </g>
              )
            })}
          </g>
        </g>
      </svg>

      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        {KNOWLEDGE_ENTITY_TYPES.map((kind) => (
          <span key={kind} className="flex items-center gap-1.5">
            <span className={cn('size-2.5 rounded-full', TYPE_FILL[kind])} aria-hidden="true" />
            {KNOWLEDGE_ENTITY_META[kind].label}
          </span>
        ))}
        <span className="ml-auto flex items-center gap-1.5">
          <Crosshair aria-hidden="true" className="size-3" />
          Select a node to focus its neighbours. Drag to pan, scroll to zoom.
        </span>
      </div>
    </div>
  )
}
