import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { fetchOverview, subscribe } from "./api"
import type { Snapshot } from "./types"

type Mode = "red" | "blue"

interface Store {
  snap: Snapshot | null
  live: boolean
  mode: Mode
  setMode: (m: Mode) => void
}

const Ctx = createContext<Store>({ snap: null, live: false, mode: "red", setMode: () => {} })

export function StoreProvider({ children }: { children: ReactNode }) {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [live, setLive] = useState(false)
  const [mode, setMode] = useState<Mode>(() =>
    (localStorage.getItem("medusa-mode") as Mode) || "red"
  )

  useEffect(() => {
    fetchOverview().then(setSnap).catch(() => {})
    const unsub = subscribe(
      (s) => {
        setSnap(s)
        setLive(true)
      },
      () => setLive(false)
    )
    return unsub
  }, [])

  useEffect(() => {
    localStorage.setItem("medusa-mode", mode)
  }, [mode])

  return <Ctx.Provider value={{ snap, live, mode, setMode }}>{children}</Ctx.Provider>
}

export function useStore(): Store {
  return useContext(Ctx)
}
