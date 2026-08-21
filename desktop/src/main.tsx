import "@fontsource-variable/geist";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/fira-code/400.css";
import "@fontsource/fira-code/500.css";
import "./design.css";
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { Connect } from "./screens/Connect";
import { Shell } from "./components/Shell";

function App() {
  const [gw, setGw] = useState<{ base: string; token: string } | null>(null);
  if (!gw) return <Connect onConnected={setGw} />;
  return <Shell base={gw.base} token={gw.token} onDisconnect={() => setGw(null)} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
