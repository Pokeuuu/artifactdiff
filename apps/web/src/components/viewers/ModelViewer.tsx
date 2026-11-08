// Synchronized old/new glTF viewer: one shared camera, two canvases, object list, highlighting.
import { Eye, EyeOff, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { ErrorState } from '@/components/common'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useFetched } from '@/lib/hooks'
import type { ChangeKind, ComparisonDetail } from '@/lib/types'
import { cn } from '@/lib/utils'

const COLORS: Record<string, number> = {
  added: 0x3fb68b,
  removed: 0xef6461,
  modified: 0xd9a33b,
  renamed: 0x3987e5,
}
const SELECT = 0x22d3ee

function parse(buffer: ArrayBuffer): Promise<THREE.Group> {
  return new Promise((resolve, reject) =>
    new GLTFLoader().parse(
      buffer,
      '',
      (g) => resolve(g.scene),
      (e) => reject(new Error(e?.message ?? 'Could not load model')),
    ),
  )
}

/** Named objects that (directly or through children) carry geometry. */
function namedObjects(root: THREE.Object3D): Map<string, THREE.Object3D> {
  const out = new Map<string, THREE.Object3D>()
  root.traverse((o) => {
    if (o === root || !o.name) return
    let hasMesh = false
    o.traverse((c) => (hasMesh ||= (c as THREE.Mesh).isMesh === true))
    if (hasMesh && !out.has(o.name)) out.set(o.name, o)
  })
  return out
}

interface Side {
  renderer: THREE.WebGLRenderer
  scene: THREE.Scene
  controls: OrbitControls
  objects: Map<string, THREE.Object3D>
  helpers: THREE.Object3D[]
}

export default function ModelViewer({ detail }: { detail: ComparisonDetail }) {
  const base = `/api/v1/comparisons/${detail.id}/files`
  const a = useFetched(detail.inputs_available ? `${base}/old` : null, 'buffer')
  const b = useFetched(detail.inputs_available ? `${base}/new` : null, 'buffer')
  const refA = useRef<HTMLDivElement>(null)
  const refB = useRef<HTMLDivElement>(null)
  const sides = useRef<Side[]>([])
  const camera = useRef<THREE.PerspectiveCamera | null>(null)
  const home = useRef<{ pos: THREE.Vector3; target: THREE.Vector3 } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [names, setNames] = useState<{ name: string; inOld: boolean; inNew: boolean }[]>([])
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<string | null>(null)
  const [highlight, setHighlight] = useState(true)
  const [ready, setReady] = useState(0)
  const resizeObserver = useRef<ResizeObserver | null>(null)

  // Change kind per object name, from node:/mesh: paths in the result.
  const changeOf = useMemo(() => {
    const m = new Map<string, ChangeKind>()
    for (const c of detail.result?.changes ?? []) {
      const match = c.path.match(/^(?:node|mesh):([^/]+)/)
      if (!match?.[1]) continue
      const name = match[1]
      if (!m.has(name) || c.kind === 'added' || c.kind === 'removed') m.set(name, c.kind)
      if (c.kind === 'renamed' && typeof c.old_value === 'string') m.set(c.old_value, 'renamed')
    }
    return m
  }, [detail.result])

  const render = useCallback(
    () => sides.current.forEach((s) => camera.current && s.renderer.render(s.scene, camera.current)),
    [],
  )

  useEffect(() => {
    if (!a.data || !b.data || !refA.current || !refB.current) return
    let disposed = false
    const containers = [refA.current, refB.current]
    Promise.all([parse(a.data), parse(b.data)])
      .then(([sa, sb]) => {
        if (disposed) return
        const cam = new THREE.PerspectiveCamera(45, 1, 0.01, 1e6)
        camera.current = cam
        const box = new THREE.Box3().setFromObject(sa).union(new THREE.Box3().setFromObject(sb))
        const center = box.getCenter(new THREE.Vector3())
        const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3)
        cam.position.copy(center).add(new THREE.Vector3(1, 0.8, 1.4).normalize().multiplyScalar(radius * 2.6))
        cam.near = radius / 100
        cam.far = radius * 100
        cam.lookAt(center)
        home.current = { pos: cam.position.clone(), target: center.clone() }
        sides.current = [sa, sb].map((model, i) => {
          const el = containers[i]!
          const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
          renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
          el.appendChild(renderer.domElement)
          const scene = new THREE.Scene()
          scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 2.2))
          const dir = new THREE.DirectionalLight(0xffffff, 1.6)
          dir.position.set(1, 2, 1.5).multiplyScalar(radius * 4)
          scene.add(dir, model)
          const grid = new THREE.GridHelper(radius * 4, 20, 0x3a4152, 0x262b36)
          grid.position.y = box.min.y
          scene.add(grid)
          const controls = new OrbitControls(cam, renderer.domElement)
          controls.target.copy(center)
          controls.enableDamping = false
          return { renderer, scene, controls, objects: namedObjects(model), helpers: [] }
        })
        // Keep both controls' targets in sync; the camera itself is shared.
        sides.current.forEach((s, i) =>
          s.controls.addEventListener('change', () => {
            const other = sides.current[1 - i]
            if (other) other.controls.target.copy(s.controls.target)
            render()
          }),
        )
        const resize = () => {
          const w = containers[0]!.clientWidth
          const h = containers[0]!.clientHeight
          cam.aspect = w / Math.max(1, h)
          cam.updateProjectionMatrix()
          sides.current.forEach((s) => s.renderer.setSize(w, h))
          render()
        }
        const ro = new ResizeObserver(resize)
        ro.observe(containers[0]!)
        resize()
        // Click-to-select via raycasting.
        sides.current.forEach((s) => {
          s.renderer.domElement.addEventListener('pointerup', (ev) => {
            if (ev.button !== 0) return
            const rect = s.renderer.domElement.getBoundingClientRect()
            const ndc = new THREE.Vector2(
              ((ev.clientX - rect.left) / rect.width) * 2 - 1,
              -((ev.clientY - rect.top) / rect.height) * 2 + 1,
            )
            const ray = new THREE.Raycaster()
            ray.setFromCamera(ndc, cam)
            const hit = ray.intersectObjects([...s.objects.values()], true)[0]
            if (!hit) return
            const name = [...s.objects.entries()].find(([, o]) => {
              let found = false
              o.traverse((c) => (found ||= c === hit.object))
              return found
            })?.[0]
            setSelected((cur) => (name && cur !== name ? name : null))
          })
        })
        const all = new Set([...sides.current[0]!.objects.keys(), ...sides.current[1]!.objects.keys()])
        setNames(
          [...all].sort().map((name) => ({
            name,
            inOld: sides.current[0]!.objects.has(name),
            inNew: sides.current[1]!.objects.has(name),
          })),
        )
        resizeObserver.current = ro
        setReady((r) => r + 1)
      })
      .catch((e: Error) => !disposed && setError(e.message))
    return () => {
      disposed = true
      resizeObserver.current?.disconnect()
      for (const s of sides.current) {
        s.controls.dispose()
        s.scene.traverse((o) => {
          const mesh = o as THREE.Mesh
          mesh.geometry?.dispose?.()
          const mat = mesh.material as THREE.Material | THREE.Material[] | undefined
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose())
          else mat?.dispose?.()
        })
        s.renderer.dispose()
        s.renderer.domElement.remove()
      }
      sides.current = []
    }
  }, [a.data, b.data, render])

  // Visibility, selection and difference highlighting.
  useEffect(() => {
    if (!ready) return
    sides.current.forEach((s, i) => {
      s.helpers.forEach((h) => s.scene.remove(h))
      s.helpers = []
      for (const [name, obj] of s.objects) {
        obj.visible = !hidden.has(name)
        const kind = changeOf.get(name)
        const show =
          name === selected ||
          (highlight && kind && !(kind === 'added' && i === 0) && !(kind === 'removed' && i === 1))
        if (!show || !obj.visible) continue
        const helper = new THREE.BoxHelper(obj, name === selected ? SELECT : COLORS[kind ?? 'modified'])
        s.scene.add(helper)
        s.helpers.push(helper)
      }
    })
    render()
  }, [ready, hidden, selected, highlight, changeOf, render])

  if (!detail.inputs_available)
    return (
      <p className="text-sm text-fg-3">
        3D view unavailable: uploaded files were deleted by the retention policy.
      </p>
    )
  if (a.error || b.error || error) return <ErrorState error={a.error ?? b.error ?? error} />

  const loading = !ready
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-3 text-[12.5px] text-fg-2">
        <label className="flex cursor-pointer items-center gap-1.5">
          <input
            type="checkbox"
            checked={highlight}
            onChange={(e) => setHighlight(e.target.checked)}
            className="accent-[var(--accent)]"
          />
          Highlight differences
        </label>
        <span className="flex items-center gap-3 text-fg-3">
          {(['added', 'removed', 'modified'] as const).map((k) => (
            <span key={k} className="flex items-center gap-1">
              <span
                className="size-2.5 rounded-sm"
                style={{ background: `#${COLORS[k]!.toString(16).padStart(6, '0')}` }}
              />
              {k}
            </span>
          ))}
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={() => {
            const cam = camera.current
            if (!cam || !home.current) return
            cam.position.copy(home.current.pos)
            sides.current.forEach((s) => s.controls.target.copy(home.current!.target))
            sides.current[0]?.controls.update()
            render()
          }}
        >
          <RotateCcw /> Reset view
        </Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-[1fr_1fr_220px]">
        {[
          ['Old', refA],
          ['New', refB],
        ].map(([label, ref]) => (
          <div key={label as string} className="relative">
            <span className="absolute top-2 left-2 z-10 rounded bg-panel/80 px-1.5 py-0.5 text-[11.5px] text-fg-2">
              {label as string}
            </span>
            <div
              ref={ref as React.RefObject<HTMLDivElement>}
              className="h-[360px] overflow-hidden rounded-lg border border-line bg-panel-2 [&>canvas]:block"
              data-testid={`viewer-${(label as string).toLowerCase()}`}
            />
            {loading && <Skeleton className="absolute inset-0 h-[360px]" />}
          </div>
        ))}
        <div className="max-h-[360px] overflow-y-auto rounded-lg border border-line">
          <p className="sticky top-0 border-b border-line bg-panel-2 px-3 py-2 text-[12px] font-medium text-fg-3">
            Objects ({names.length})
          </p>
          <ul className="text-[12.5px]">
            {names.map(({ name, inOld, inNew }) => {
              const kind = changeOf.get(name)
              return (
                <li
                  key={name}
                  className={cn(
                    'flex items-center gap-2 px-3 py-1.5 hover:bg-panel-2/60',
                    selected === name && 'bg-accent-soft',
                  )}
                >
                  <button
                    className="min-w-0 flex-1 cursor-pointer truncate text-left font-mono"
                    onClick={() => setSelected((s) => (s === name ? null : name))}
                    title={name}
                  >
                    {name}
                  </button>
                  {kind && (
                    <span
                      className="size-2 shrink-0 rounded-full"
                      style={{
                        background: `#${(COLORS[kind] ?? COLORS.modified!).toString(16).padStart(6, '0')}`,
                      }}
                      title={kind}
                    />
                  )}
                  <span className="shrink-0 text-[11px] text-fg-3">
                    {inOld && inNew ? '' : inOld ? 'old' : 'new'}
                  </span>
                  <button
                    className="shrink-0 cursor-pointer text-fg-3 hover:text-fg"
                    aria-label={hidden.has(name) ? `Show ${name}` : `Hide ${name}`}
                    onClick={() =>
                      setHidden((h) => {
                        const n = new Set(h)
                        if (n.has(name)) n.delete(name)
                        else n.add(name)
                        return n
                      })
                    }
                  >
                    {hidden.has(name) ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      </div>
      <p className="mt-2 text-[12px] text-fg-3">
        Drag to rotate · right-drag or shift-drag to pan · scroll to zoom · click an object to select it. Both
        views share one camera.
      </p>
    </div>
  )
}
