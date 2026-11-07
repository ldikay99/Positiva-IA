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
  chispa: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z",
  regla: "M4 7h16M4 12h10M4 17h6M17 14l3 3-3 3",
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
  atras: "M15 5l-7 7 7 7",
  menos: "M5 12h14",
  menu: "M4 6h16M4 12h16M4 18h16",
  lado: "M4 4h16v16H4zM15 4v16",
  filtro: "M4 5h16l-6 8v5l-4 2v-7z",
  ruta: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM6 15V9a4 4 0 0 1 4-4h6M18 9v6a4 4 0 0 1-4 4H8",
  documento: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6",
  escaner: "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16M8 8h8M8 16h5",
  base: "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3",
  escudo: "M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6zM9 12l2 2 4-4",
  enviar: "M4 12l16-8-6 16-3-7zM11 13l9-9",
  bandera: "M5 21V4M5 4h11l-2 4 2 4H5",
  nota: "M5 4h14v11l-5 5H5zM14 20v-5h5",
  mira: "M12 3v3M12 18v3M3 12h3M18 12h3M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  candado: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3",
  abierto: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 7.5-2",
  mapa: "M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2zM9 4v14M15 6v14",
  copiar: "M9 9h11v11H9zM5 15H4V4h11v1",
  papelera: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6",
  editar: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  reloj: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  persona: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5v.5",
  flecha: "M5 12h14M13 6l6 6-6 6",
  abajo: "M6 9l6 6 6-6",
  derecha: "M9 6l6 6-6 6",
  expandir: "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7",
  pausa: "M8 5v14M16 5v14",
  punto: "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  terminal: "M4 5h16v14H4zM7 9l3 3-3 3M12 15h5",
} as const;

export type NombreIcono = keyof typeof RUTAS;

export function Icono({ n, tam = 16, ...resto }: { n: NombreIcono; tam?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...resto}>
      <path d={RUTAS[n]} />
    </svg>
  );
}
