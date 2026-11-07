import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { Icono } from "../iconos.tsx";

/**
 * El desplegable de la plataforma: reemplaza al <select> nativo en todas partes.
 * Se abre en una capa propia (no lo corta ningún contenedor con overflow), se
 * acomoda arriba o abajo según el espacio, y se maneja con teclado como un
 * listbox: flechas, Inicio/Fin, Enter, Escape y escribir para saltar.
 */

export interface OpcionSelector<T extends string | number = string> {
  valor: T;
  etiqueta: ReactNode;
  /** Texto para buscar con el teclado y para el lector de pantalla. */
  texto?: string;
  detalle?: ReactNode;
  deshabilitada?: boolean;
}

export function Selector<T extends string | number = string>({
  valor,
  opciones,
  alCambiar,
  etiqueta,
  placeholder = "Elegir…",
  deshabilitado = false,
  tamano = "normal",
  tono = "claro",
  className = "",
  titulo,
  ancho,
}: {
  valor: T | null | undefined;
  opciones: OpcionSelector<T>[];
  alCambiar: (v: T) => void;
  /** Nombre accesible del control. */
  etiqueta: string;
  placeholder?: string;
  deshabilitado?: boolean;
  tamano?: "chico" | "normal";
  tono?: "claro" | "oscuro" | "plano";
  className?: string;
  titulo?: string;
  ancho?: number;
}) {
  const [abierto, setAbierto] = useState(false);
  const [activo, setActivo] = useState(0);
  const [pos, setPos] = useState<{ x: number; y: number; w: number; arriba: boolean; alto: number } | null>(null);
  const boton = useRef<HTMLButtonElement>(null);
  const lista = useRef<HTMLUListElement>(null);
  const buscar = useRef({ texto: "", t: 0 });
  const id = useId();
  const elegida = opciones.find((o) => o.valor === valor);

  const ubicar = () => {
    const r = boton.current?.getBoundingClientRect();
    if (!r) return;
    const abajo = window.innerHeight - r.bottom - 8;
    const arriba = r.top - 8;
    const deseado = Math.min(320, opciones.length * 38 + 12);
    const haciaArriba = abajo < Math.min(deseado, 200) && arriba > abajo;
    const w = Math.max(r.width, ancho ?? 0, 160);
    setPos({ x: Math.max(8, Math.min(r.left, window.innerWidth - w - 8)), y: haciaArriba ? r.top - 6 : r.bottom + 6, w, arriba: haciaArriba, alto: Math.max(120, Math.min(deseado, haciaArriba ? arriba : abajo)) });
  };

  const abrir = () => {
    if (deshabilitado) return;
    ubicar();
    setActivo(Math.max(0, opciones.findIndex((o) => o.valor === valor)));
    setAbierto(true);
  };
  const cerrar = (devolverFoco = true) => {
    setAbierto(false);
    if (devolverFoco) boton.current?.focus();
  };
  const elegir = (i: number) => {
    const o = opciones[i];
    if (!o || o.deshabilitada) return;
    alCambiar(o.valor);
    cerrar();
  };

  useLayoutEffect(() => {
    if (!abierto) return;
    lista.current?.querySelector<HTMLElement>(`[data-i="${activo}"]`)?.scrollIntoView({ block: "nearest" });
  }, [abierto, activo]);

  useEffect(() => {
    if (!abierto) return;
    const fuera = (e: PointerEvent) => {
      if (!boton.current?.contains(e.target as Node) && !lista.current?.contains(e.target as Node)) cerrar(false);
    };
    const mover = () => ubicar();
    window.addEventListener("pointerdown", fuera, true);
    window.addEventListener("resize", mover);
    window.addEventListener("scroll", mover, true);
    return () => {
      window.removeEventListener("pointerdown", fuera, true);
      window.removeEventListener("resize", mover);
      window.removeEventListener("scroll", mover, true);
    };
  });

  const mover = (paso: number) => {
    let i = activo;
    for (let n = 0; n < opciones.length; n++) {
      i = (i + paso + opciones.length) % opciones.length;
      if (!opciones[i]!.deshabilitada) break;
    }
    setActivo(i);
  };

  const teclas = (e: React.KeyboardEvent) => {
    if (deshabilitado) return;
    if (!abierto) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        abrir();
      }
      return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); mover(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); mover(-1); }
    else if (e.key === "Home") { e.preventDefault(); setActivo(0); }
    else if (e.key === "End") { e.preventDefault(); setActivo(opciones.length - 1); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); elegir(activo); }
    else if (e.key === "Escape" || e.key === "Tab") { if (e.key === "Escape") e.preventDefault(); cerrar(e.key === "Escape"); }
    else if (e.key.length === 1) {
      const ahora = Date.now();
      buscar.current = { texto: (ahora - buscar.current.t < 700 ? buscar.current.texto : "") + e.key.toLowerCase(), t: ahora };
      const i = opciones.findIndex((o) => (o.texto ?? String(o.etiqueta)).toLowerCase().normalize("NFD").replace(/\p{M}/gu, "").startsWith(buscar.current.texto));
      if (i >= 0) setActivo(i);
    }
  };

  return (
    <>
      <button
        ref={boton}
        type="button"
        className={`selector ${tamano} tono-${tono} ${abierto ? "abierto" : ""} ${className}`}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={abierto}
        aria-controls={abierto ? id : undefined}
        aria-activedescendant={abierto ? `${id}-${activo}` : undefined}
        aria-label={etiqueta}
        title={titulo}
        disabled={deshabilitado}
        onClick={() => (abierto ? cerrar() : abrir())}
        onKeyDown={teclas}
      >
        <span className={`selector-valor ${elegida ? "" : "vacio"}`}>{elegida ? elegida.etiqueta : placeholder}</span>
        <Icono n="abajo" tam={13} className="selector-flecha" />
      </button>
      {createPortal(
        <AnimatePresence>
          {abierto && pos && (
            <motion.ul
              ref={lista}
              id={id}
              role="listbox"
              aria-label={etiqueta}
              className="selector-lista"
              style={{ left: pos.x, width: pos.w, maxHeight: pos.alto, ...(pos.arriba ? { bottom: window.innerHeight - pos.y } : { top: pos.y }) }}
              initial={{ opacity: 0, y: pos.arriba ? 6 : -6, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, transition: { duration: 0.1 } }}
              transition={{ duration: 0.14, ease: [0.23, 1, 0.32, 1] }}
            >
              {opciones.map((o, i) => (
                <li
                  key={String(o.valor)}
                  id={`${id}-${i}`}
                  data-i={i}
                  role="option"
                  aria-selected={o.valor === valor}
                  aria-disabled={o.deshabilitada || undefined}
                  className={`selector-opcion ${i === activo ? "activa" : ""} ${o.valor === valor ? "elegida" : ""} ${o.deshabilitada ? "deshabilitada" : ""}`}
                  onPointerEnter={() => setActivo(i)}
                  onPointerDown={(e) => e.preventDefault()}
                  onClick={() => elegir(i)}
                >
                  <span className="selector-texto">
                    <span>{o.etiqueta}</span>
                    {o.detalle && <small>{o.detalle}</small>}
                  </span>
                  {o.valor === valor && <Icono n="check" tam={14} />}
                </li>
              ))}
            </motion.ul>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
