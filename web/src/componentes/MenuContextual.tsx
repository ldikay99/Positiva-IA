import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion } from "motion/react";
import { Icono, type NombreIcono } from "../iconos.tsx";

export type Opcion =
  | { separador: true }
  | { titulo: string }
  | {
      etiqueta: string;
      icono?: NombreIcono;
      atajo?: string;
      pista?: string;
      peligro?: boolean;
      marcado?: boolean;
      deshabilitado?: boolean;
      alHacer: () => void;
    };

/** Menú de clic derecho: se acomoda dentro de la ventana y se cierra con Esc o al hacer clic fuera. */
export function MenuContextual({ x, y, opciones, cerrar }: { x: number; y: number; opciones: Opcion[]; cerrar: () => void }) {
  const caja = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });

  useLayoutEffect(() => {
    const r = caja.current?.getBoundingClientRect();
    if (!r) return;
    setPos({ x: Math.max(8, Math.min(x, window.innerWidth - r.width - 8)), y: Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) });
  }, [x, y]);

  useEffect(() => {
    const fuera = (e: MouseEvent) => {
      if (!caja.current?.contains(e.target as Node)) cerrar();
    };
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && cerrar();
    window.addEventListener("pointerdown", fuera, true);
    window.addEventListener("keydown", tecla);
    window.addEventListener("wheel", cerrar, { passive: true });
    window.addEventListener("resize", cerrar);
    return () => {
      window.removeEventListener("pointerdown", fuera, true);
      window.removeEventListener("keydown", tecla);
      window.removeEventListener("wheel", cerrar);
      window.removeEventListener("resize", cerrar);
    };
  }, [cerrar]);

  return createPortal(
    <motion.div
      ref={caja}
      className="menu-contextual"
      role="menu"
      style={{ left: pos.x, top: pos.y }}
      initial={{ opacity: 0, scale: 0.96, y: -4 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.12, ease: "easeOut" }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {opciones.map((o, i) => {
        if ("separador" in o) return <div key={i} className="menu-separador" />;
        if ("titulo" in o) return <div key={i} className="menu-titulo">{o.titulo}</div>;
        return (
          <button
            key={i}
            role="menuitem"
            className={`menu-opcion ${o.peligro ? "peligro" : ""}`}
            disabled={o.deshabilitado}
            title={o.pista}
            onClick={() => {
              cerrar();
              o.alHacer();
            }}
          >
            <span className="menu-icono">{o.marcado ? <Icono n="ok" tam={14} /> : o.icono ? <Icono n={o.icono} tam={14} /> : null}</span>
            <span className="crece">{o.etiqueta}</span>
            {o.atajo && <kbd>{o.atajo}</kbd>}
          </button>
        );
      })}
    </motion.div>,
    document.body,
  );
}
