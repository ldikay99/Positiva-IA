"""
Servicio para búsqueda de juzgados en la base de datos de producción
"""
import psycopg2
from typing import Optional

try:
    from Tutelas.config import DATABASE_CONFIG
    from Tutelas.models.correo import Juzgado
except ImportError:
    from config import DATABASE_CONFIG
    from models.correo import Juzgado


class JuzgadosService:
    def __init__(self):
        self.db_config = DATABASE_CONFIG
        self._conn = None
        self._conectar()

    def _conectar(self):
        """Establece conexión con la base de datos de producción"""
        try:
            self._conn = psycopg2.connect(
                host=self.db_config["host"],
                port=self.db_config["port"],
                dbname=self.db_config["database"],
                user=self.db_config["user"],
                password=self.db_config["password"],
                connect_timeout=10,
            )
            self._conn.autocommit = True
            # Contar juzgados disponibles
            with self._conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM juzgados")
                total = cur.fetchone()[0]
            print(f"Conectado a BD producción - {total} juzgados disponibles")
        except Exception as e:
            print(f"Error conectando a BD de juzgados: {e}")
            self._conn = None

    def _get_conn(self):
        """Obtiene conexión activa, reconecta si es necesario"""
        if self._conn is None or self._conn.closed:
            self._conectar()
        return self._conn

    def _row_to_juzgado(self, row) -> Juzgado:
        """Convierte una fila de la BD en un objeto Juzgado"""
        return Juzgado(
            id_juzgado=row[0],
            nombre=row[1] or "",
            direccion=row[2] or "",
            pais=row[3] or "Colombia",
            departamento=row[4] or "",
            municipio=row[5] or "",
            codigo=row[6] or "",
            telefono=row[7] or "",
            celular=row[8] or "",
            correo=row[9] or "",
        )

    def buscar_por_codigo(self, codigo: str) -> Optional[Juzgado]:
        """Busca un juzgado por su código (primeros 12 dígitos del radicado).

        La comparacion descarta todo lo que no sea digito en el codigo guardado:
        algunos registros traen espacios o caracteres invisibles que impiden la
        coincidencia exacta y hacen aparecer como no registrado un despacho que si
        existe.
        """
        conn = self._get_conn()
        if not conn or not codigo:
            return None

        codigo = "".join(ch for ch in str(codigo) if ch.isdigit())
        if not codigo:
            return None

        query = """
            SELECT id_juzgado, nombre, direccion, pais, departamento,
                   municipio, codigo, telefono, celular, correo
            FROM juzgados
            WHERE regexp_replace(codigo, '[^0-9]', '', 'g') = %s
            LIMIT 1
        """

        try:
            with conn.cursor() as cur:
                cur.execute(query, (codigo,))
                row = cur.fetchone()
                if row:
                    return self._row_to_juzgado(row)
        except Exception as e:
            print(f"Error buscando juzgado por código: {e}")
            self._conn = None  # Forzar reconexión en siguiente intento

        return None

    def buscar_por_nombre(self, nombre: str, ciudad: str = None) -> Optional[Juzgado]:
        """Busca un juzgado por nombre y opcionalmente ciudad"""
        conn = self._get_conn()
        if not conn or not nombre:
            return None

        if ciudad:
            query = """
                SELECT id_juzgado, nombre, direccion, pais, departamento,
                       municipio, codigo, telefono, celular, correo
                FROM juzgados
                WHERE UPPER(nombre) LIKE %s AND UPPER(municipio) LIKE %s
                LIMIT 1
            """
            params = (f"%{nombre.upper()}%", f"%{ciudad.upper()}%")
        else:
            query = """
                SELECT id_juzgado, nombre, direccion, pais, departamento,
                       municipio, codigo, telefono, celular, correo
                FROM juzgados
                WHERE UPPER(nombre) LIKE %s
                LIMIT 1
            """
            params = (f"%{nombre.upper()}%",)

        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_juzgado(row)
        except Exception as e:
            print(f"Error buscando juzgado por nombre: {e}")
            self._conn = None

        return None

    def buscar_juzgado(self, codigo: str = None, nombre: str = None, ciudad: str = None) -> Optional[Juzgado]:
        """
        Busca un juzgado usando código primero, luego nombre+ciudad como fallback.
        """
        # Intentar primero por código
        if codigo:
            juzgado = self.buscar_por_codigo(codigo)
            if juzgado:
                return juzgado

        # Fallback: buscar por nombre y ciudad
        if nombre:
            return self.buscar_por_nombre(nombre, ciudad)

        return None

    def close(self):
        """Cierra la conexión a la base de datos"""
        if self._conn and not self._conn.closed:
            self._conn.close()
            print("   [JUZGADOS] Conexión a BD cerrada")
