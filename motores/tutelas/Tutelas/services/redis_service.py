"""
Servicio para deduplicación de correos usando Redis
"""
import hashlib
import re
import redis
from typing import Optional

try:
    from Tutelas.config import REDIS_CONFIG, MODO_TEST, OMITIR_ESCRITURAS
    from Tutelas.test_recorder import get_recorder
except ImportError:
    from config import REDIS_CONFIG, MODO_TEST, OMITIR_ESCRITURAS
    from test_recorder import get_recorder


class RedisService:
    def __init__(self):
        self.config = REDIS_CONFIG
        self.client = None

    def _get_client(self) -> redis.Redis:
        """Obtiene o crea la conexión a Redis"""
        if self.client is None:
            self.client = redis.Redis(
                host=self.config["host"],
                port=self.config["port"],
                username=self.config["username"],
                password=self.config["password"],
                decode_responses=True,
            )
        return self.client

    def generar_hash(self, texto: str) -> str:
        """Genera un hash SHA256 del texto (radicado)"""
        if not texto:
            return ""
        return hashlib.sha256(texto.encode()).hexdigest()

    def existe_hash(self, hash_value: str) -> bool:
        """Verifica si un hash ya existe en Redis"""
        # MODO_TEST: no consultar dedup -> tratar todo como NO duplicado (reprocesar).
        if MODO_TEST:
            return False
        if not hash_value:
            return False

        key = f"{self.config['prefix']}{hash_value}"
        result = self._get_client().get(key)
        return result is not None

    def guardar_hash(self, hash_value: str, valor: str = "1") -> bool:
        """Guarda un hash en Redis"""
        # MODO_TEST: NO persistir (evita que el agente de prod salte estos correos).
        # MODO_UAT: tampoco persiste (no se debe tocar el dedup compartido con PRD).
        if OMITIR_ESCRITURAS:
            get_recorder().registrar("REDIS_SAVE", destino="redis", detalle={"hash": hash_value})
            return True
        if not hash_value:
            return False

        key = f"{self.config['prefix']}{hash_value}"
        self._get_client().set(key, valor)
        return True

    def verificar_duplicado(self, radicado: str) -> tuple[str, bool]:
        """
        Verifica si un radicado ya fue procesado.
        Retorna (hash, es_duplicado)
        """
        hash_value = self.generar_hash(radicado)
        es_duplicado = self.existe_hash(hash_value)
        return hash_value, es_duplicado

    def marcar_procesado(self, hash_value: str) -> bool:
        """Marca un correo como procesado en Redis"""
        return self.guardar_hash(hash_value)

    def reclamar(self, texto: str, ttl_segundos: int = 2592000) -> tuple[str, bool]:
        """Reclama ATOMICAMENTE un radicado con SET NX (set-if-not-exists).

        Retorna (hash, reclamado):
          - reclamado=True  -> ESTA ejecucion gano el claim y debe radicar.
          - reclamado=False -> ya estaba reclamado por otra ejecucion/corrida
                               -> es duplicado, saltar.

        A diferencia de verificar_duplicado (GET) + marcar_procesado (SET), aqui el
        chequeo y la marca son UNA sola operacion atomica, asi dos ejecuciones
        concurrentes no pueden ambas "ganar" el mismo radicado. TTL por defecto 30
        dias (ventana de dedup; ademas evita que un claim quede pegado si una
        ejecucion muere sin liberar).
        """
        hash_value = self.generar_hash(texto)
        # MODO_TEST: no persistir -> tratar como reclamado (procesar), igual que
        # existe_hash/guardar_hash en dry-run, para no saltar correos.
        if MODO_TEST:
            get_recorder().registrar("REDIS_CLAIM", destino="redis", detalle={"hash": hash_value})
            return hash_value, True
        if not hash_value:
            return hash_value, True
        key = f"{self.config['prefix']}{hash_value}"
        creado = self._get_client().set(key, "1", nx=True, ex=ttl_segundos)
        return hash_value, bool(creado)

    # Llave del cerrojo que impide que dos ejecuciones del job procesen el buzon
    # al mismo tiempo. El TTL la libera sola si una ejecucion muere sin cerrarla.
    LLAVE_EJECUCION = "ejecucion:en-curso"

    def tomar_cerrojo(self, identificador: str, ttl_segundos: int = 3600) -> bool:
        """Reserva el buzon para esta ejecucion. True si quedo reservado.

        Cuando una corrida se demora mas que el intervalo del programador, la
        siguiente arranca sobre el mismo buzon: una mueve los correos mientras la
        otra trabaja con identificadores que ya cambiaron, de donde salen los
        errores al etiquetar, los avisos repetidos y las radicaciones duplicadas.
        Con el cerrojo la segunda ejecucion termina de inmediato.
        """
        if MODO_TEST:
            return True
        key = f"{self.config['prefix']}{self.LLAVE_EJECUCION}"
        return bool(self._get_client().set(key, identificador, nx=True, ex=ttl_segundos))

    def titular_cerrojo(self) -> Optional[str]:
        """Identificador de la ejecucion que tiene el buzon reservado."""
        if MODO_TEST:
            return None
        return self._get_client().get(f"{self.config['prefix']}{self.LLAVE_EJECUCION}")

    def soltar_cerrojo(self, identificador: str) -> bool:
        """Libera el buzon, solo si la reserva sigue siendo de esta ejecucion."""
        if MODO_TEST:
            return True
        key = f"{self.config['prefix']}{self.LLAVE_EJECUCION}"
        cliente = self._get_client()
        if cliente.get(key) == identificador:
            cliente.delete(key)
            return True
        return False

    # Imagenes incrustadas del cuerpo del correo (firmas, logos). Outlook las nombra
    # image.png, image001.png, image002.jpg, etc. Aparecen o desaparecen segun quien
    # reenvie el hilo, por lo que no identifican el documento de la tutela.
    _RE_IMAGEN_FIRMA = re.compile(r"^image\d*\.(png|jpg|jpeg|gif|bmp)$", re.IGNORECASE)

    @classmethod
    def _cuenta_para_huella(cls, nombre: str, contenido: bytes) -> bool:
        """Indica si un adjunto identifica el documento de la tutela.

        Excluye los adjuntos sin contenido, el PDF consolidado que genera este
        servicio (lleva fecha y remitente del hilo) y las imagenes incrustadas
        del cuerpo del correo.
        """
        if not contenido:
            return False
        if nombre == "correo_completo.pdf":
            return False
        return not cls._RE_IMAGEN_FIRMA.match(nombre or "")

    @classmethod
    def huella_documentos(cls, adjuntos) -> str:
        """Huella del contenido de los documentos de la tutela.

        `adjuntos` es una lista de tuplas (nombre, contenido_bytes, content_type).
        Hashea con SHA256 el contenido binario de los adjuntos que identifican el
        documento (ver `_cuenta_para_huella`) y concatena los hashes ordenados, por
        lo que la huella es independiente del nombre del archivo y del orden de los
        adjuntos. Si ningun adjunto califica, retorna cadena vacia.
        """
        hashes = [
            hashlib.sha256(contenido).hexdigest()
            for nombre, contenido, _ct in adjuntos
            if cls._cuenta_para_huella(nombre, contenido)
        ]
        if not hashes:
            return ""
        return ":".join(sorted(hashes))

    # Elementos que cambian entre dos versiones del mismo documento y no hacen
    # parte de su contenido: espacios, fechas, horas y numeros de pagina.
    _RE_ESPACIOS = re.compile(r"\s+")
    _RE_FECHA = re.compile(
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
        r"|\d{1,2}\s+de\s+[a-zñ]+\s+de\s+\d{4}",
        re.IGNORECASE,
    )
    _RE_HORA = re.compile(r"\d{1,2}:\d{2}(:\d{2})?\s*(a\.?m\.?|p\.?m\.?)?", re.IGNORECASE)
    _RE_PAGINA = re.compile(r"p[aá]gina\s*\d+\s*(de\s*\d+)?", re.IGNORECASE)
    # Conectores que quedan sueltos al retirar una fecha u hora ("generado el ...
    # a las ..."). Sin esto, dos versiones del mismo documento con distinto
    # formato de fecha dejarian residuos diferentes.
    _RE_CONECTORES = re.compile(
        r"\b(generad[oa]|expedid[oa]|impres[oa]|emitid[oa])?\s*"
        r"(el|del|a\s+las|siendo|hora|fecha)\b\s*",
        re.IGNORECASE,
    )
    _RE_NO_ALFANUM = re.compile(r"[^a-z0-9ñáéíóúü ]+")

    @classmethod
    def normalizar_texto(cls, texto: str) -> str:
        """Texto reducido a su contenido comparable.

        Pasa a minusculas, retira fechas, horas, numeracion de paginas y los
        conectores que las acompanan, descarta la puntuacion y colapsa los
        espacios. Dos versiones del mismo documento (por ejemplo un PDF
        regenerado o vuelto a escanear) producen el mismo resultado.
        """
        if not texto:
            return ""
        t = texto.lower()
        t = cls._RE_FECHA.sub(" ", t)
        t = cls._RE_HORA.sub(" ", t)
        t = cls._RE_PAGINA.sub(" ", t)
        t = cls._RE_CONECTORES.sub(" ", t)
        t = cls._RE_NO_ALFANUM.sub(" ", t)
        t = cls._RE_ESPACIOS.sub(" ", t)
        return t.strip()

    @classmethod
    def huella_texto(cls, textos) -> str:
        """Huella del texto de los documentos de la tutela.

        `textos` es una lista de cadenas extraidas de los documentos. Normaliza
        cada una (ver `normalizar_texto`), descarta las que quedan demasiado
        cortas para identificar un documento y hashea el resultado ordenado.
        Retorna cadena vacia si ningun texto es utilizable.
        """
        hashes = []
        for texto in textos:
            limpio = cls.normalizar_texto(texto)
            if len(limpio) < cls.MIN_CARACTERES_TEXTO:
                continue
            hashes.append(hashlib.sha256(limpio.encode("utf-8")).hexdigest())
        if not hashes:
            return ""
        return ":".join(sorted(hashes))

    # Longitud minima del texto normalizado para considerarlo identificatorio.
    # Por debajo de este umbral el texto no distingue un documento de otro.
    MIN_CARACTERES_TEXTO = 200

    def liberar(self, hash_value: str) -> bool:
        """Libera un claim (borra la llave) para que el correo pueda reintentarse.
        Se usa cuando la radicacion falla de forma CLARA (sin registro creado)."""
        if MODO_TEST or not hash_value:
            return True
        key = f"{self.config['prefix']}{hash_value}"
        self._get_client().delete(key)
        return True

    def close(self):
        """Cierra la conexión a Redis"""
        if self.client:
            self.client.close()
            self.client = None
