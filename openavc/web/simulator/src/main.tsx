import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import App from './App.tsx'
import { SessionGate } from './SessionGate.tsx'

// The gate runs before App so no API call is made without a credential — the
// 401s are what used to raise the browser's own sign-in dialog.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SessionGate>
      <App />
    </SessionGate>
  </StrictMode>,
)
