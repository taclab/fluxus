import { useEffect, useState } from 'react'

import { getSyncState, subscribe, type SyncState } from './engine'

export function useSyncState(): SyncState {
  const [state, setState] = useState<SyncState>(getSyncState())
  useEffect(() => subscribe(setState), [])
  return state
}
