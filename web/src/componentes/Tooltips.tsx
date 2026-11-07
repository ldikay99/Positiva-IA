import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Tooltips propios para toda la plataforma. Cualquier elemento con `title`
 * muestra este globo en lugar del del navegador: el texto se guarda en
 * `data-titulo` mientras el puntero (o el foco del teclado) está encima, y se
 * devuelve al salir, así los lectores de pantalla y el código existente no cambian.
 */
export function Tooltips() {
  const [globo, setGlobo] = useState<{ texto: string; x: number; y: number; arriba: boolean } | null>(null);
  const actual = useRef<HTMLElement | null>(null);
  const reloj = useRef<number>(0);

  useEffect(() => {
    const soltar = () => {
      window.clearTimeout(reloj.current);
      const el = actual.current;
      if (el && el.dataset.titulo !== undefined) {
        el.setAttribute("title", el.dataset.titulo);
        delete el.dataset.titulo;
      }
      actual.current = null;
      setGlobo(null);
    };
    const tomar = (el: HTMLElement, inmediato: boolean) => {
      if (actual.current === el) return;
      soltar();
      const texto = el.getAttribute("title");
      if (!texto?.trim()) return;
      actual.current = el;
      el.dataset.titulo = texto;
      el.removeAttribute("title"); // que no salga también el del navegador
      reloj.current = window.setTimeout(() => {
        if (actual.current !== el || !el.isConnected) return;
        const r = el.getBoundingClientRect();
        const arriba = r.top > 60;
        setGlobo({ texto, x: Math.min(Math.max(r.left + r.width / 2, 12), window.innerWidth - 12), y: arriba ? r.top - 8 : r.bottom + 8, arriba });
      }, inmediato ? 0 : 450);
    };
    const sobre = (e: PointerEvent) => {
      // Con el dedo no hay «encima»: un globo que queda pegado tras tocar solo estorba.
      if (e.pointerType === "touch") return;
      const el = (e.target as Element | null)?.closest?.("[title]") as HTMLElement | null;
      if (el && !(el instanceof SVGElement)) tomar(el, false);
      else if (actual.current && !actual.current.contains(e.target as Node)) soltar();
    };
    const foco = (e: FocusEvent) => {
      const el = (e.target as Element | null)?.closest?.("[title]") as HTMLElement | null;
      if (el && (e.target as HTMLElement).matches(":focus-visible")) tomar(el, true);
    };
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && soltar();
    document.addEventListener("pointerover", sobre, true);
    document.addEventListener("focusin", foco, true);
    document.addEventListener("focusout", soltar, true);
    document.addEventListener("pointerdown", soltar, true);
    document.addEventListener("keydown", tecla, true);
    window.addEventListener("scroll", soltar, true);
    return () => {
      soltar();
      document.removeEventListener("pointerover", sobre, true);
      document.removeEventListener("focusin", foco, true);
      document.removeEventListener("focusout", soltar, true);
      document.removeEventListener("pointerdown", soltar, true);
      document.removeEventListener("keydown", tecla, true);
      window.removeEventListener("scroll", soltar, true);
    };
  }, []);

  if (!globo) return null;
  return createPortal(<Globo {...globo} />, document.body);
}

/** El globo se mide al dibujarse y se corre para que nunca quede fuera de la ventana. */
function Globo({ texto, x, y, arriba }: { texto: string; x: number; y: number; arriba: boolean }) {
  const caja = useRef<HTMLDivElement>(null);
  const [corrimiento, setCorrimiento] = useState(0);
  useLayoutEffect(() => {
    const r = caja.current?.getBoundingClientRect();
    if (!r) return;
    const margen = 8;
    if (r.left < margen) setCorrimiento(margen - r.left);
    else if (r.right > window.innerWidth - margen) setCorrimiento(window.innerWidth - margen - r.right);
    else setCorrimiento(0);
  }, [texto, x, y]);
  return (
    <div ref={caja} role="tooltip" className={`globo ${arriba ? "arriba" : "abajo"}`} style={{ left: x + corrimiento, top: y, ["--flecha" as string]: `${-corrimiento}px` }}>
      {texto}
    </div>
  );
}
