import { create } from 'zustand'

interface SystemStatus {
  cpu: { percent: number; cores: number }
  memory: { total_gb: number; used_gb: number; percent: number }
  disk: { total_gb: number; used_gb: number; percent: number }
  load_avg: { '1min': number; '5min': number; '15min': number }
  hostname: string
  os: string
  arch: string
}

interface SystemStore {
  status: SystemStatus | null
  loading: boolean
  fetchStatus: () => Promise<void>
}

export const useSystemStore = create<SystemStore>((set) => ({
  status: null,
  loading: false,

  fetchStatus: async () => {
    try {
      const res = await fetch('/api/system/status')
      if (res.ok) {
        const data = await res.json()
        set({ status: data })
      }
    } catch (err) {
      console.error('Failed to fetch system status:', err)
    }
  },
}))
