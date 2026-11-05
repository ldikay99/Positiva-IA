# Generador del catálogo

Lee el código de `motor/` sin ejecutarlo y arma `catalogo/catalogo.json`: nodos del flujo,
reglas, parámetros con su valor actual y si se pueden ajustar en caliente, herramientas del
agente, proveedores y variables de entorno.

```bash
python extraer.py constantes.json   # valores de las constantes del motor
python build.py                     # escribe ../catalogo.json
```

Las descripciones de nodos, reglas y proveedores están escritas en `build.py`; hay que revisarlas
cuando cambie el orden de `Orquestador.procesar` o se agreguen etapas.
