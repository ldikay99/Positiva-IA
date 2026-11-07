import type { ReactNode } from "react";

/**
 * Markdown de las respuestas de Vera, construido como nodos de React (nunca se
 * inyecta HTML): títulos, listas, tablas, citas, bloques de código, **negrita**,
 * *cursiva* y `código`.
 */

function enLinea(texto: string, clave: string): ReactNode[] {
  const partes = texto.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g);
  return partes.map((t, i) => {
    const k = `${clave}-${i}`;
    if (t.startsWith("**") && t.endsWith("**") && t.length > 4) return <b key={k}>{t.slice(2, -2)}</b>;
    if (t.startsWith("`") && t.endsWith("`") && t.length > 2) return <code key={k}>{t.slice(1, -1)}</code>;
    if ((t.startsWith("*") && t.endsWith("*") && t.length > 2) || (t.startsWith("_") && t.endsWith("_") && t.length > 2)) return <i key={k}>{t.slice(1, -1)}</i>;
    return t;
  });
}

const celdas = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export function Markdown({ texto }: { texto: string }) {
  const lineas = texto.replace(/\r/g, "").split("\n");
  const bloques: ReactNode[] = [];
  let i = 0;
  while (i < lineas.length) {
    const l = lineas[i]!;
    const k = `b${i}`;
    if (!l.trim()) {
      i++;
      continue;
    }
    if (l.trim().startsWith("```")) {
      const codigo: string[] = [];
      i++;
      while (i < lineas.length && !lineas[i]!.trim().startsWith("```")) codigo.push(lineas[i++]!);
      i++;
      bloques.push(<pre key={k} className="md-codigo"><code>{codigo.join("\n")}</code></pre>);
      continue;
    }
    const titulo = /^(#{1,4})\s+(.*)$/.exec(l);
    if (titulo) {
      bloques.push(<p key={k} className={`md-titulo n${titulo[1]!.length}`}>{enLinea(titulo[2]!, k)}</p>);
      i++;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lineas.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lineas[i + 1]!)) {
      const cab = celdas(l);
      i += 2;
      const filas: string[][] = [];
      while (i < lineas.length && /^\s*\|.*\|\s*$/.test(lineas[i]!)) filas.push(celdas(lineas[i++]!));
      bloques.push(
        <div key={k} className="md-tabla">
          <table>
            <thead><tr>{cab.map((c, j) => <th key={j}>{enLinea(c, `${k}h${j}`)}</th>)}</tr></thead>
            <tbody>{filas.map((f, r) => <tr key={r}>{f.map((c, j) => <td key={j}>{enLinea(c, `${k}${r}-${j}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      const ordenada = /^\s*\d+[.)]\s+/.test(l);
      const items: string[] = [];
      while (i < lineas.length && /^\s*([-*•]|\d+[.)])\s+/.test(lineas[i]!)) items.push(lineas[i++]!.replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      const hijos = items.map((t, j) => <li key={j}>{enLinea(t, `${k}-${j}`)}</li>);
      bloques.push(ordenada ? <ol key={k}>{hijos}</ol> : <ul key={k}>{hijos}</ul>);
      continue;
    }
    if (l.startsWith(">")) {
      const cita: string[] = [];
      while (i < lineas.length && lineas[i]!.startsWith(">")) cita.push(lineas[i++]!.replace(/^>\s?/, ""));
      bloques.push(<blockquote key={k}>{enLinea(cita.join(" "), k)}</blockquote>);
      continue;
    }
    const parrafo: string[] = [];
    while (i < lineas.length && lineas[i]!.trim() && !/^(#{1,4}\s|```|\s*([-*•]|\d+[.)])\s|>|\s*\|)/.test(lineas[i]!)) parrafo.push(lineas[i++]!);
    if (!parrafo.length) parrafo.push(lineas[i++]!);
    bloques.push(<p key={k}>{parrafo.flatMap((t, j) => (j ? [<br key={`${k}br${j}`} />, ...enLinea(t, `${k}-${j}`)] : enLinea(t, `${k}-${j}`)))}</p>);
  }
  return <>{bloques}</>;
}
