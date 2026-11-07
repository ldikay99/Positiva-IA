import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { Tooltips } from "./componentes/Tooltips.tsx";
import "./diseno.css";
import "./plataforma.css";
import "./editor.css";
import "./producto.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
    <Tooltips />
  </StrictMode>,
);
