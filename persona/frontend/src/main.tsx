import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App";
import { BrandProvider } from "./context/BrandContext";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("#root element not found in index.html");
}

createRoot(rootEl).render(
  <StrictMode>
    <BrandProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </BrandProvider>
  </StrictMode>,
);
