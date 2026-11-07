"""
Conectores de los flujos declarativos: correo (IMAP, SMTP, Microsoft Graph),
Google Drive, bases de datos (PostgreSQL, MySQL, SQLite), hojas de cálculo,
el espacio de archivos, la tabla integrada y el evaluador de expresiones.

Todos comparten los límites de `base.py`: destinos bloqueados (Positiva,
SGDEA, metadatos, IP privadas), tiempos de espera, topes de filas y la regla
de que nada escribe afuera sin una credencial con `permitir_escritura`.
"""
