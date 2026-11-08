import { useEffect, useState } from 'react'
import { fetchBlob } from './api'

/** Object URL for a protected resource; revoked on unmount. Returns [url, error]. */
export function useBlobUrl(url: string | null | undefined, type?: string): [string | null, string | null] {
  const [state, setState] = useState<{ src: string | null; url: string | null; error: string | null }>({
    src: null,
    url: null,
    error: null,
  })
  useEffect(() => {
    if (!url) return
    let objectUrl: string | null = null
    let cancelled = false
    fetchBlob(url)
      .then((b) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(type ? new Blob([b], { type }) : b)
        setState({ src: url, url: objectUrl, error: null })
      })
      .catch((e: Error) => !cancelled && setState({ src: url, url: null, error: e.message }))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url, type])
  if (!url || state.src !== url) return [null, null]
  return [state.url, state.error]
}

/** Fetch a protected resource as text or ArrayBuffer. */
export function useFetched<T extends 'text' | 'buffer'>(
  url: string | null,
  as: T,
): { data: (T extends 'text' ? string : ArrayBuffer) | null; error: string | null; loading: boolean } {
  type D = T extends 'text' ? string : ArrayBuffer
  const [state, setState] = useState<{ src: string | null; data: D | null; error: string | null }>({
    src: null,
    data: null,
    error: null,
  })
  useEffect(() => {
    if (!url) return
    let cancelled = false
    fetchBlob(url)
      .then((b): Promise<string | ArrayBuffer> => (as === 'text' ? b.text() : b.arrayBuffer()))
      .then((d) => !cancelled && setState({ src: url, data: d as unknown as D, error: null }))
      .catch((e: Error) => !cancelled && setState({ src: url, data: null, error: e.message }))
    return () => {
      cancelled = true
    }
  }, [url, as])
  const current = url !== null && state.src === url
  return {
    data: current ? state.data : null,
    error: current ? state.error : null,
    loading: !!url && !current,
  }
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}
