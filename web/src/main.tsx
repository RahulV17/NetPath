/**
 * NetPath · Network Data Path Lab — entry point.
 * The 3D interactive lab is the sole frontend.
 */
import React from 'react'
import ReactDOM from 'react-dom/client'
import LabPage from './lab/LabPage'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LabPage />
  </React.StrictMode>,
)
