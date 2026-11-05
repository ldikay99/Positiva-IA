import { useEffect, useMemo, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { EditorView } from "@codemirror/view";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { esRegex, type Regex } from "../parametros.ts";

const tema = EditorView.theme({
  "&": { fontSize: "12.5px", backgroundColor: "var(--superficie-2)", borderRadius: "8px" },
  ".cm-content": { fontFamily: "var(--mono)", padding: "8px 0" },
  ".cm-gutters": { backgroundColor: "transparent", border: "none", color: "var(--texto-3)" },
  "&.cm-focused": { outline: "2px solid var(--azul-borde)" },
  ".cm-activeLine": { backgroundColor: "rgba(47,111,214,0.05)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent" },
});

export function Codigo({ valor, alCambiar, lenguaje = "texto", alto = "auto", minAlto = "80px", soloLectura = false }: { valor: string; alCambiar?: (v: string) => void; lenguaje?: "json" | "markdown" | "texto"; alto?: string; minAlto?: string; soloLectura?: boolean }) {
  const ext = useMemo(() => [tema, EditorView.lineWrapping, ...(lenguaje === "json" ? [json()] : lenguaje === "markdown" ? [markdown()] : [])], [lenguaje]);
  return (
    <CodeMirror
      value={valor}
      onChange={alCambiar}
      extensions={ext}
      height={alto}
      minHeight={minAlto}
      maxHeight="60vh"
      editable={!soloLectura}
      basicSetup={{ lineNumbers: lenguaje !== "texto", foldGutter: false, highlightActiveLine: !soloLectura, autocompletion: false, searchKeymap: true }}
    />
  );
}

/** Texto de muestra para probar reglas: el cuerpo del correo abierto o de la corrida visible. */
function useTextoDeMuestra(): string {
  const foco = usar((s) => s.foco);
  const corrida = usar((s) => (s.corridaVisible ? s.corridas[s.corridaVisible] : undefined));
  const caso = foco?.tipo === "correo" ? foco.caso : corrida?.caso;
  const [texto, setTexto] = useState("");
  useEffect(() => {
    if (!caso) return;
    void pedir<{ asunto: string; cuerpo?: string }>(`/api/bandeja/${encodeURIComponent(caso)}`).then((c) => setTexto(`${c.asunto}\n\n${c.cuerpo ?? ""}`)).catch(() => undefined);
  }, [caso]);
  return texto;
}

interface Coincidencia {
  inicio: number;
  fin: number;
  texto: string;
  grupos: (string | null)[];
}

export function EditorRegex({ valor, alCambiar }: { valor: Regex; alCambiar: (v: Regex) => void }) {
  const muestra = useTextoDeMuestra();
  const [texto, setTexto] = useState("");
  const [resultado, setResultado] = useState<{ ok: boolean; error?: string; coincidencias?: Coincidencia[] } | null>(null);
  useEffect(() => setTexto((t) => t || muestra), [muestra]);

  useEffect(() => {
    const espera = setTimeout(() => {
      if (!texto) return setResultado(null);
      void pedir<{ ok: boolean; error?: string; coincidencias?: Coincidencia[] }>("/api/reglas/probar", { method: "POST", body: JSON.stringify({ patron: valor.regex, flags: valor.flags, texto }) })
        .then(setResultado)
        .catch((e: Error) => setResultado({ ok: false, error: e.message }));
    }, 350);
    return () => clearTimeout(espera);
  }, [valor.regex, valor.flags, texto]);

  const marcado = useMemo(() => {
    if (!resultado?.ok || !resultado.coincidencias?.length) return null;
    const partes: React.ReactNode[] = [];
    let i = 0;
    resultado.coincidencias.slice(0, 80).forEach((m, k) => {
      if (m.inicio > i) partes.push(texto.slice(i, m.inicio));
      partes.push(<mark key={k}>{texto.slice(m.inicio, m.fin) || "∅"}</mark>);
      i = m.fin;
    });
    partes.push(texto.slice(i));
    return partes;
  }, [resultado, texto]);

  return (
    <div className="pila">
      <Codigo valor={valor.regex} alCambiar={(regex) => alCambiar({ ...valor, regex })} minAlto="38px" />
      <div className="fila">
        {["IGNORECASE", "MULTILINE", "DOTALL"].map((f) => {
          const activos = valor.flags ? valor.flags.split("|") : [];
          return (
            <span key={f} className={`ficha ${activos.includes(f) ? "activa" : ""}`} title="Las banderas se fijan en el código; se muestran como referencia">
              {f.toLowerCase()}
            </span>
          );
        })}
        <span className="espacio" />
        {resultado && (resultado.ok ? <span className="pastilla verde">{resultado.coincidencias?.length ?? 0} coincidencias</span> : <span className="pastilla rojo">{resultado.error}</span>)}
      </div>
      <details className="probador" open={Boolean(muestra)}>
        <summary>Probar contra un texto {muestra ? "(el del correo abierto)" : ""}</summary>
        <textarea className="campo mono" rows={4} value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="Pega un texto o abre un correo de la bandeja" />
        {marcado && <div className="resaltado">{marcado}</div>}
      </details>
    </div>
  );
}

export function EditorLista({ valor, alCambiar }: { valor: unknown[]; alCambiar: (v: unknown[]) => void }) {
  const [nuevo, setNuevo] = useState("");
  const simples = valor.every((x) => typeof x === "string" || typeof x === "number");
  if (!simples) return <EditorJson valor={valor} alCambiar={(v) => Array.isArray(v) && alCambiar(v)} />;
  return (
    <div className="pila">
      <div className="fichas">
        {valor.map((x, i) => (
          <span key={`${String(x)}-${i}`} className="ficha editable">
            {String(x)}
            <button aria-label="Quitar" onClick={() => alCambiar(valor.filter((_, j) => j !== i))}><Icono n="cerrar" tam={11} /></button>
          </span>
        ))}
      </div>
      <form
        className="fila"
        onSubmit={(e) => {
          e.preventDefault();
          if (!nuevo.trim()) return;
          alCambiar([...valor, typeof valor[0] === "number" ? Number(nuevo) : nuevo.trim()]);
          setNuevo("");
        }}
      >
        <input className="campo" value={nuevo} onChange={(e) => setNuevo(e.target.value)} placeholder="Agregar elemento y Enter" />
      </form>
    </div>
  );
}

export function EditorMapa({ valor, alCambiar }: { valor: Record<string, unknown>; alCambiar: (v: Record<string, unknown>) => void }) {
  const entradas = Object.entries(valor);
  const escalares = entradas.every(([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v) || esRegex(v));
  if (!escalares || entradas.length > 120) return <EditorJson valor={valor} alCambiar={(v) => v && typeof v === "object" && !Array.isArray(v) && alCambiar(v as Record<string, unknown>)} />;
  return (
    <div className="mapa">
      {entradas.map(([k, v]) => (
        <div key={k} className="mapa-fila">
          <code className="mapa-clave" title={k}>{k}</code>
          {esRegex(v) ? (
            <input className="campo mono" value={v.regex} onChange={(e) => alCambiar({ ...valor, [k]: { ...v, regex: e.target.value } })} />
          ) : typeof v === "number" ? (
            <input className="campo mono" type="number" step="any" value={v} onChange={(e) => alCambiar({ ...valor, [k]: Number(e.target.value) })} />
          ) : typeof v === "boolean" ? (
            <Interruptor valor={v} alCambiar={(b) => alCambiar({ ...valor, [k]: b })} />
          ) : String(v ?? "").length > 80 ? (
            <textarea className="campo" rows={3} value={String(v ?? "")} onChange={(e) => alCambiar({ ...valor, [k]: e.target.value })} />
          ) : (
            <input className="campo" value={String(v ?? "")} onChange={(e) => alCambiar({ ...valor, [k]: e.target.value })} />
          )}
        </div>
      ))}
    </div>
  );
}

export function EditorJson({ valor, alCambiar }: { valor: unknown; alCambiar: (v: unknown) => void }) {
  const [texto, setTexto] = useState(() => JSON.stringify(valor, null, 2));
  const [error, setError] = useState("");
  useEffect(() => setTexto(JSON.stringify(valor, null, 2)), [valor]);
  return (
    <div className="pila">
      <Codigo
        valor={texto}
        lenguaje="json"
        alCambiar={(t) => {
          setTexto(t);
          try {
            alCambiar(JSON.parse(t));
            setError("");
          } catch {
            setError("JSON inválido");
          }
        }}
      />
      {error && <span className="pastilla rojo">{error}</span>}
    </div>
  );
}

export function Interruptor({ valor, alCambiar }: { valor: boolean; alCambiar: (v: boolean) => void }) {
  return (
    <button role="switch" aria-checked={valor} className={`interruptor ${valor ? "si" : ""}`} onClick={() => alCambiar(!valor)}>
      <span />
    </button>
  );
}

export function Deslizador({ valor, alCambiar, min = 0, max = 1, paso = 0.01 }: { valor: number; alCambiar: (v: number) => void; min?: number; max?: number; paso?: number }) {
  return (
    <div className="deslizador">
      <input type="range" min={min} max={max} step={paso} value={valor} onChange={(e) => alCambiar(Number(e.target.value))} style={{ ["--p" as string]: `${((valor - min) / (max - min)) * 100}%` }} />
      <input className="campo mono" type="number" min={min} max={max} step={paso} value={valor} onChange={(e) => alCambiar(Number(e.target.value))} />
    </div>
  );
}
