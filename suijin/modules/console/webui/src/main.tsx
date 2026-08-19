import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { HashRouter } from "react-router-dom"
import { StoreProvider } from "./store"
import App from "./App"
import "./styles/global.css"
import "./styles/shell.css"
import "./styles/charts.css"
import "./styles/views.css"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <StoreProvider>
        <App />
      </StoreProvider>
    </HashRouter>
  </StrictMode>
)
