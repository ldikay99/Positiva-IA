# Roles, permisos y aprobación de escrituras

En Vera cada persona pertenece a una organización y tiene un rol dentro de ella. El rol define qué puede hacer; la
organización define sobre qué datos. Nadie ve ni usa flujos, ejecuciones, archivos, conversaciones o conexiones de
otra organización.

## Roles

| Rol | Para quién | Qué hace |
|---|---|---|
| **Administrador** | Responsable de la organización en Vera | Gestiona personas, roles, conexiones, escrituras y límites, además de todo lo del editor |
| **Editor** | Quien construye automatizaciones | Crea, modifica, versiona y corre flujos, solo o con Vera |
| **Operador** | Quien usa las automatizaciones en su día a día | Corre los flujos existentes y revisa sus resultados |
| **Lector** | Quien consulta o audita | Ve flujos, ejecuciones y resultados sin cambiar ni correr nada |

Cada persona tiene un solo rol por organización. Una organización siempre tiene al menos un administrador.

## Matriz de permisos

| Acción | Administrador | Editor | Operador | Lector |
|---|:-:|:-:|:-:|:-:|
| Ver flujos, versiones y su detalle | ✓ | ✓ | ✓ | ✓ |
| Ver ejecuciones, resultados y archivos de salida | ✓ | ✓ | ✓ | ✓ |
| Correr un flujo | ✓ | ✓ | ✓ | — |
| Usar a Vera por chat y voz | ✓ | ✓ | ✓ (consultar y correr) | ✓ (consultar) |
| Crear, editar y duplicar flujos; volver a una versión | ✓ | ✓ | — | — |
| Definir variables del entorno de un flujo (no secretas) | ✓ | ✓ | — | — |
| Usar una conexión existente en un flujo | ✓ | ✓ | — | — |
| Borrar un flujo (con confirmación) | ✓ | ✓ | — | — |
| Crear conexiones e ingresar secretos | ✓ | — | — | — |
| Habilitar o retirar la escritura de una conexión | ✓ | — | — | — |
| Invitar personas, asignar roles y retirarlas | ✓ | — | — | — |
| Configurar modelos de IA y límites de uso | ✓ | — | — | — |
| Ver la auditoría de la organización | ✓ | — | — | — |

Lo que Vera hace por una persona se rige por el rol de esa persona: con un operador corre flujos pero no los edita; con
un lector solo consulta.

## Política de aprobación de escrituras hacia sistemas externos

1. **Retenida por defecto.** Toda escritura hacia afuera (enviar o responder correo, crear eventos, subir archivos,
   ejecutar SQL de escritura, HTTP distinto de GET, publicar mensajes, enviar a firmar) queda retenida y armada en el
   detalle de la ejecución mientras la conexión no tenga la escritura habilitada.
2. **La habilita una persona.** Solo un administrador habilita la escritura de una conexión, con una confirmación
   explícita en pantalla. Vera no puede habilitarla, ni ingresar o leer secretos: abre la pantalla y la persona decide.
3. **Por conexión, no global.** Habilitar una conexión no habilita otras. Retirar el permiso aplica desde la siguiente
   ejecución.
4. **Lista de bloqueo fija.** Ningún nodo escribe en hosts vetados (metadatos de la nube, direcciones internas no
   autorizadas ni los sistemas que la organización declare protegidos), aunque la conexión tenga la escritura
   habilitada.
5. **Trazabilidad.** La auditoría registra quién habilitó o retiró cada escritura, cuándo, y cada escritura que salió
   o quedó retenida, sin guardar secretos ni el contenido de los mensajes.

## Sesiones y cuentas

- Entrada con contraseña cifrada y segundo factor; bloqueo temporal tras varios intentos fallidos.
- Recuperación de la cuenta con verificación por correo; cerrar sesión invalida la sesión en el servidor.
- Retirar a una persona de la organización cierra sus sesiones y le quita el acceso de inmediato; sus flujos siguen
  siendo de la organización.
