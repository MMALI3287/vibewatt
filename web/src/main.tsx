import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App";
import { AccountScope } from "./components/AccountScope";
import "./styles.css";

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <AccountScope>
      {/* React Router 7 commits history in a transition by default; URL-backed
          inputs then revert to the committed value mid-keystroke. */}
      <BrowserRouter useTransitions={false}>
        <App />
      </BrowserRouter>
    </AccountScope>
  </StrictMode>,
);
