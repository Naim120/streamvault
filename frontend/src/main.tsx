import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

if (typeof window !== 'undefined') {
  // Gracefully reload page when dynamic chunks 404 due to new production deployments
  window.addEventListener('vite:preloadError', (event) => {
    event.preventDefault();
    window.location.reload();
  });

  console.log(
    '%cSTREAMVAULT%c\n\nSTOP! Do not paste or execute any code or scripts here. Doing so could compromise your account, session tokens, or private media library. If someone told you to paste something here, it is a scam.\n\nStreamVault Media Engine v2.5.0',
    'color: #E50914; font-size: 32px; font-weight: 900; -webkit-text-stroke: 1px #000; text-shadow: 0 0 10px rgba(229,9,20,0.5);',
    'color: #ffffff; font-size: 14px; font-weight: bold; line-height: 1.5;'
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
