import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import Caregiver from "./pages/Caregiver";
import Resident from "./pages/Resident";
import "./styles.css";

function Home() {
  return (
    <main className="home">
      <h1>DoorSight</h1>
      <ul>
        <li><Link to="/resident">Resident view</Link></li>
        <li><Link to="/caregiver">Caregiver view</Link></li>
      </ul>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/resident" element={<Resident />} />
        <Route path="/caregiver" element={<Caregiver />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
