import type { SVGProps } from "react";

const RUTAS = {
  flujo: "M5 6h4v4H5zM15 14h4v4h-4zM9 8h3a2 2 0 0 1 2 2v4a2 2 0 0 0 2 2",
  bandeja: "M4 13l2-8h12l2 8M4 13v6h16v-6M4 13h5l1 2h4l1-2h5",
  corridas: "M4 19V9M10 19V5M16 19v-7M22 19H2",
  logs: "M5 5h14M5 10h14M5 15h9M5 20h6",
  herramientas: "M14 6a4 4 0 0 1-5 5l-5 5 3 3 5-5a4 4 0 0 1 5-5l-2-2 2-2a4 4 0 0 0-3 1z",
  proveedores: "M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4",
  ajustes: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
  play: "M7 5l12 7-12 7z",
  stop: "M6 6h12v12H6z",
  buscar: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM16 16l5 5",
  cerrar: "M6 6l12 12M18 6L6 18",
  ok: "M5 12l5 5 9-10",
  alerta: "M12 4l9 16H3zM12 10v4M12 17v.5",
  chispa: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  regla: "M4 7h16M4 12h10M4 17h6",
  encuadrar: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  reordenar: "M4 4h6v6H4zM14 14h6v6h-6zM14 4h6v6h-6zM4 14h6v6H4z",
  historial: "M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4M12 8v4l3 2",
  descargar: "M12 4v12M7 11l5 5 5-5M5 20h14",
  adjunto: "M15 7l-6 6a2 2 0 0 0 3 3l7-7a4 4 0 0 0-6-6l-7 7a6 6 0 0 0 8 8l6-6",
  ojo: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  deshacer: "M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3",
  capas: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  correo: "M3 6h18v12H3zM3 6l9 7 9-7",
  rayo: "M13 3L5 13h6l-1 8 8-10h-6z",
  mas: "M12 5v14M5 12h14",
  menu: "M4 6h16M4 12h16M4 18h16",
  lado: "M4 4h16v16H4zM15 4v16",
} as const;

export type NombreIcono = keyof typeof RUTAS;

export function Icono({ n, tam = 16, ...resto }: { n: NombreIcono; tam?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...resto}>
      <path d={RUTAS[n]} />
    </svg>
  );
}
