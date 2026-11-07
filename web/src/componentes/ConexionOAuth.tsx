import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { pedir, usar } from "../estado.ts";
import { Icono } from "../iconos.tsx";
import { usarEditor, type Credencial } from "../editor/almacen.ts";

/**
 * Lo que tienen de más algunas conexiones en su tarjeta:
 * - OAuth (Google y Microsoft 365): «Conectar», la cuenta conectada con sus permisos y «Desconectar».
 * - Toda conexión: «Probar conexión», con el resultado ahí mismo.
 * - API (OpenAPI): «Analizar», con las operaciones que quedan como pasos.
 * El consentimiento se abre en una ventana aparte porque es la página del proveedor; al volver,
 * la ventana avisa por postMessage y, por si no, la lista se vuelve a pedir cada 2 s hasta 3 min.
 */

export const TIPOS_OAUTH = new Set(["google_oauth", "microsoft_oauth"]);
const PROVEEDOR: Record<string, string> = { google_oauth: "Google", microsoft_oauth: "Microsoft" };

function fecha(t?: number): string {
  if (!t) return "";
  return new Date(t).toLocaleString("es-CO", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Un permiso de OAuth en corto (sin https://www.googleapis.com/auth/). */
const corto = (a: string) => a.replace("https://www.googleapis.com/auth/", "").replace("https://graph.microsoft.com/", "");

/** La URI de redirección que se registra en Google Cloud o Entra ID, con su botón de copiar. */
export function AyudaRedireccion({ tipo }: { tipo: string }) {
  const [uri, setUri] = useState("");
  const [copiado, setCopiado] = useState(false);
  useEffect(() => {
    if (!TIPOS_OAUTH.has(tipo)) return;
    void pedir<{ redirect_uri: string }>("/api/oauth/redireccion").then((r) => setUri(r.redirect_uri)).catch(() => setUri(""));
  }, [tipo]);
  if (!TIPOS_OAUTH.has(tipo) || !uri) return null;
  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(uri);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1600);
    } catch {
      /* sin portapapeles */
    }
  };
  return (
    <div className="co-redireccion">
      <span className="texto-2 chico">URI de redirección para registrar en {tipo === "google_oauth" ? "Google Cloud (cliente OAuth «Aplicación web»)" : "Entra ID (plataforma Web)"}</span>
      <div className="co-copiar">
        <code className="mono">{uri}</code>
        <button type="button" className="boton chico" onClick={() => void copiar()}><Icono n={copiado ? "check" : "copiar"} tam={13} />{copiado ? "Copiada" : "Copiar"}</button>
      </div>
    </div>
  );
}

type Prueba = { ok: boolean; detalle?: string; error?: string; cuenta?: string } | null;

/** El bloque de OAuth, «Probar conexión» y «Analizar» de una tarjeta de conexión. */
export function AccionesDeConexion({ c }: { c: Credencial }) {
  const cargar = usarEditor((s) => s.cargarCredenciales);
  const avisar = usar((s) => s.avisar);
  const oauth = TIPOS_OAUTH.has(c.tipo);
  const proveedor = PROVEEDOR[c.tipo] ?? "";
  const [conectando, setConectando] = useState(false);
  const [desconectar, setDesconectar] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const [prueba, setPrueba] = useState<Prueba>(null);
  const [probando, setProbando] = useState(false);
  const [analisis, setAnalisis] = useState<Analisis | null>(null);
  const [analizando, setAnalizando] = useState(false);
  const espera = useRef<number | null>(null);

  // al volver del proveedor: la ventana avisa (postMessage) y se recarga la lista
  useEffect(() => {
    if (!oauth) return;
    const oir = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return;
      const d = e.data as { tipo?: string; credencial?: string; ok?: boolean; error?: string };
      if (d?.tipo !== "oauth" || d.credencial !== c.nombre) return;
      parar();
      void cargar();
      avisar(d.ok ? `«${c.nombre}» quedó conectada con ${proveedor}` : `No se pudo conectar «${c.nombre}»: ${d.error ?? ""}`, d.ok ? "ok" : "error");
    };
    window.addEventListener("message", oir);
    return () => window.removeEventListener("message", oir);
  }, [oauth, c.nombre, proveedor, cargar, avisar]);
  useEffect(() => () => parar(), []);

  // mientras se conecta, se vuelve a pedir la lista cada 2 s hasta 3 min (por si la ventana no puede avisar)
  useEffect(() => {
    if (conectando && c.conexion?.conectada && (c.conexion.conectada_en ?? 0) > Date.now() - 3 * 60_000) parar();
  }, [conectando, c.conexion]);

  function parar() {
    if (espera.current !== null) window.clearInterval(espera.current);
    espera.current = null;
    setConectando(false);
  }

  const conectar = async () => {
    try {
      const r = await pedir<{ url: string }>(`/api/oauth/${encodeURIComponent(c.nombre)}/iniciar?json=1`);
      const ventana = window.open(r.url, `oauth-${c.nombre}`, "popup,width=520,height=700");
      if (!ventana) window.open(r.url, "_blank", "noopener");
      setConectando(true);
      const inicio = Date.now();
      if (espera.current !== null) window.clearInterval(espera.current);
      espera.current = window.setInterval(() => {
        if (Date.now() - inicio > 3 * 60_000) return parar();
        void cargar();
      }, 2000);
    } catch (e) {
      avisar((e as Error).message, "error");
    }
  };

  const hacerDesconexion = async () => {
    setOcupado(true);
    try {
      const r = await pedir<{ nota?: string }>(`/api/oauth/${encodeURIComponent(c.nombre)}/revocar`, { method: "POST" });
      avisar(r.nota ?? `«${c.nombre}» quedó desconectada`, "info");
      setDesconectar(false);
      setPrueba(null);
      await cargar();
    } catch (e) {
      avisar((e as Error).message, "error");
    } finally {
      setOcupado(false);
    }
  };

  const probar = async () => {
    setProbando(true);
    setPrueba(null);
    try {
      setPrueba(await pedir<NonNullable<Prueba>>(`/api/credenciales/${encodeURIComponent(c.nombre)}/probar`, { method: "POST" }));
      if (oauth) void cargar();
    } catch (e) {
      setPrueba({ ok: false, error: (e as Error).message });
    } finally {
      setProbando(false);
    }
  };

  const analizar = async () => {
    setAnalizando(true);
    try {
      setAnalisis(await pedir<Analisis>("/api/conectores/openapi/analizar", { method: "POST", body: JSON.stringify({ credencial: c.nombre }) }));
    } catch (e) {
      setAnalisis({ ok: false, error: (e as Error).message });
    } finally {
      setAnalizando(false);
    }
  };

  const cx = c.conexion;
  return (
    <div className="co-acciones">
      {oauth && (
        <div className={`co-oauth ${cx?.conectada ? "conectada" : ""}`}>
          {cx?.conectada ? (
            <>
              <div className="co-cuenta">
                <span className="c-icono chico"><Icono n="persona" tam={14} /></span>
                <div className="crece">
                  <b className="mono">{cx.cuenta ?? "cuenta conectada"}</b>
                  <span className="texto-2 chico">Conectada con {proveedor}{cx.conectada_en ? ` el ${fecha(cx.conectada_en)}` : ""}</span>
                </div>
              </div>
              {cx.alcances?.length ? (
                <div className="co-permisos" aria-label="Permisos otorgados">
                  {cx.alcances.map((a) => <span key={a} className="ficha co-permiso">{corto(a)}</span>)}
                </div>
              ) : null}
            </>
          ) : (
            <p className="texto-2 chico co-sin">Todavía no está conectada: la persona inicia sesión en {proveedor} y acepta los permisos. Los tokens quedan cifrados en el servidor.</p>
          )}
          <div className="fila co-botones">
            <button type="button" className={`boton chico ${cx?.conectada ? "" : "primario"}`} onClick={() => void conectar()} disabled={conectando}>
              {conectando ? <span className="girando" /> : <Icono n="enlace" tam={13} />}
              {conectando ? "Esperando el regreso…" : cx?.conectada ? "Volver a conectar" : `Conectar con ${proveedor}`}
            </button>
            {cx?.conectada && <button type="button" className="boton chico" onClick={() => setDesconectar(true)}><Icono n="cerrar" tam={13} />Desconectar</button>}
          </div>
          <AnimatePresence>
            {desconectar && (
              <motion.div className="c-confirmar" role="alertdialog" aria-label="Confirmar desconexión" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
                <div className="c-confirmar-texto">
                  <Icono n="alerta" tam={16} />
                  <p>Se borran los tokens de <b>{cx?.cuenta ?? c.nombre}</b>{c.tipo === "google_oauth" ? " y se le pide a Google revocar el acceso" : ""}. Los nodos que la usan dejarán de poder conectarse y la escritura vuelve a solo lectura.</p>
                </div>
                <div className="fila c-acciones">
                  <button type="button" className="boton" onClick={() => setDesconectar(false)} autoFocus>Cancelar</button>
                  <button type="button" className="boton peligro-solido" disabled={ocupado} onClick={() => void hacerDesconexion()}>{ocupado && <span className="girando" />}Desconectar</button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
      <div className="fila co-botones">
        <button type="button" className="boton chico" onClick={() => void probar()} disabled={probando}>{probando ? <span className="girando" /> : <Icono n="play" tam={13} />}Probar conexión</button>
        {c.tipo === "api_openapi" && (
          <button type="button" className="boton chico" onClick={() => void analizar()} disabled={analizando}>{analizando ? <span className="girando" /> : <Icono n="buscar" tam={13} />}Analizar</button>
        )}
      </div>
      {prueba && (
        <p className={`co-resultado ${prueba.ok ? "ok" : "mal"}`} role="status">
          <Icono n={prueba.ok ? "ok" : "alerta"} tam={14} />
          <span>{prueba.ok ? (prueba.detalle ?? (prueba.cuenta ? `Responde: ${prueba.cuenta}` : "Responde bien")) : (prueba.error ?? prueba.detalle ?? "No respondió")}</span>
        </p>
      )}
      {analisis && <Operaciones a={analisis} cerrar={() => setAnalisis(null)} />}
    </div>
  );
}

interface Operacion { id: string; metodo: string; ruta: string; resumen?: string; parametros?: { nombre: string; en: string; obligatorio?: boolean; tipo?: string }[]; cuerpo?: unknown; escritura?: boolean }
interface Analisis { ok: boolean; error?: string; titulo?: string; version?: string; base_url?: string; operaciones?: Operacion[] }

function Operaciones({ a, cerrar }: { a: Analisis; cerrar: () => void }) {
  const [q, setQ] = useState("");
  if (!a.ok) return <p className="co-resultado mal" role="status"><Icono n="alerta" tam={14} /><span>{a.error ?? "No se pudo leer la especificación"}</span></p>;
  const ops = (a.operaciones ?? []).filter((o) => !q.trim() || `${o.id} ${o.metodo} ${o.ruta} ${o.resumen ?? ""}`.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <section className="co-ops" aria-label="Operaciones de la API">
      <header className="fila">
        <div className="crece">
          <b>{a.titulo ?? "API"}{a.version ? ` · ${a.version}` : ""}</b>
          <span className="texto-2 chico">{(a.operaciones ?? []).length} operaciones{a.base_url ? ` · ${a.base_url}` : ""} · se usan en el paso «API · operación» por su nombre</span>
        </div>
        <button type="button" className="boton-mini" aria-label="Cerrar" onClick={cerrar}><Icono n="cerrar" tam={14} /></button>
      </header>
      {(a.operaciones ?? []).length > 6 && <input className="campo" placeholder="Buscar una operación" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar una operación" />}
      <ul className="co-lista">
        {ops.map((o) => {
          const req = (o.parametros ?? []).filter((p) => p.obligatorio);
          return (
            <li key={`${o.metodo} ${o.ruta}`}>
              <span className={`co-metodo ${o.metodo === "GET" ? "lee" : "escribe"}`}>{o.metodo}</span>
              <div className="crece">
                <span className="mono co-ruta">{o.ruta}</span>
                <span className="texto-2 chico">{o.id}{o.resumen ? ` · ${o.resumen}` : ""}</span>
                {req.length > 0 && <span className="texto-3 chico">Obligatorios: {req.map((p) => `${p.nombre} (${p.en}${p.tipo ? `, ${p.tipo}` : ""})`).join(", ")}</span>}
              </div>
              <span className={`ficha ${o.metodo === "GET" ? "co-lectura" : "co-escritura"}`}>{o.metodo === "GET" ? "lectura" : "escritura"}</span>
            </li>
          );
        })}
        {!ops.length && <li className="texto-3 chico">Ninguna operación coincide.</li>}
      </ul>
    </section>
  );
}
