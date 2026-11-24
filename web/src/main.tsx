import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { LocaleRoot } from './app/LocaleRoot'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LocaleRoot />
  </StrictMode>,
)
