# DROPS-Hydro MX v0.4.10 — CHIRPS y cauce principal

## Descarga CHIRPS ClimateSERV v2

La descarga se divide en años completos. Cada año validado se guarda en `chirps_cache` dentro de la carpeta de trabajo. Al repetir la misma consulta (geometría y fechas), se reutilizan esos años; también se intenta recuperar el identificador de un trabajo pendiente durante 24 horas.

Se añaden estado por año, identificador del trabajo, reintentos de consultas ante errores temporales de conexión, cancelación y límites de espera: 30 segundos de espera de socket por petición, 10 minutos de procesamiento remoto por bloque y 20 minutos por ejecución completa, comprobados entre operaciones. Una petición activa puede demorar la cancelación hasta agotar su espera. Las solicitudes POST no se reintentan automáticamente para evitar duplicar trabajos de estado incierto. Cancelar detiene la espera local, no cancela el trabajo en el servidor.

Se comprueba que cada año tenga todas sus fechas diarias y valores válidos, incluyendo los años bisiestos. Un año incompleto genera un error explícito, en lugar de alimentar silenciosamente el ajuste de extremos. Se admiten años anteriores al actual. La caché distingue geometría, fechas, servicio y parámetros de la consulta.

Se retiró el parámetro opcional de descarga ZIP: se solicita únicamente la serie diaria promedio. La documentación del servicio resulta contradictoria sobre la cadena `false`; omitir el parámetro evita esa ambigüedad. También se corrigió la finalización: los errores al guardar el CSV ya no se ocultan detrás de un `except: pass`, y la interfaz vuelve a estar disponible tras éxito, cancelación o error.

Estos cambios mejoran diagnóstico y recuperación. No se ha confirmado la causa exacta de la descarga que falló al usuario, ni se garantiza el tiempo de respuesta de ClimateSERV. El modo CHIRPS v3 directo permanece sin modificar.

Referencia: [API oficial de ClimateSERV](https://climateserv.servirglobal.net/develop-api).

## Definición del cauce principal

Se recorta la red a la cuenca, se crea un grafo de conexiones y se identifica la ruta más larga desde una cabecera hasta el outlet seleccionado. El criterio es longitud geométrica conectada; no caudal, área aportante ni orden Strahler. Se conservan las conexiones reales de la geometría, sin unir ramas cercanas por proximidad.

En una red dendrítica la ruta es única. Si hay ciclos, se elige la mayor de las rutas mínimas a las cabeceras y se emite una advertencia. Las partes desconectadas del outlet quedan excluidas y se informa de ello. El outlet debe estar sobre la red; se admite un ajuste máximo de tres celdas de la cuadrícula de análisis. Es importante seleccionar la verdadera salida de la cuenca.

Se crea `DH_CaucePrincipal` en QGIS y un GeoPackage con nombre único en la carpeta de trabajo. Incluye:

- Longitud siguiendo el cauce y distancia recta entre extremos.
- Sinuosidad = longitud / distancia recta.
- Cotas de cabecera y salida y desnivel entre extremos.
- Pendiente longitudinal media = (cota de cabecera − cota de salida) / longitud, en m/m y porcentaje.
- Resolución del MDE empleado para estimar las cotas.

La pendiente longitudinal es la pendiente global entre extremos, no la media de pendientes absolutas de cada pequeño tramo. Las cotas se muestrean sobre la cuadrícula reproyectada. Si faltan cotas no se inventa una pendiente; si el desnivel resulta negativo, se advierte para revisar el MDE y el outlet.

La longitud calculada actualiza la entrada de longitud principal y los indicadores de morfometría que dependen de ella. Las pendientes calculadas se presentan en el informe; no se sustituyen automáticamente los parámetros de pendiente de los modelos hidrológicos existentes. Las métricas de la red ahora se limitan a su intersección con la cuenca.

## Pendiente media de la cuenca

Se reproyecta el MDE a la zona UTM de la cuenca y se calcula la pendiente del terreno por celda con el método Horn. Después se promedian los porcentajes de pendiente de las celdas cuyo centro queda dentro del polígono, excluyendo NoData. El cálculo utiliza terreno alrededor del borde antes de aplicar la máscara para reducir artefactos del recorte.

Las elevaciones del MDE deben estar en metros. Para acotar memoria y tiempo, la resolución se aumenta si la extensión de análisis supera aproximadamente cuatro millones de celdas. El informe declara la resolución efectiva y la cobertura válida; por ello, la media es una estimación dependiente de esa cuadrícula y no necesariamente la media a resolución nativa. Una cobertura menor del 95 % genera advertencia.

## Uso

1. Instala `DROPS-Hydro_MX_v0.4.10.zip` desde Complementos → Administrar e instalar complementos → Instalar a partir de ZIP. Reinicia QGIS. El archivo original no se ha modificado ni se ha instalado esta versión automáticamente.
2. En **Entradas**, selecciona cuenca, MDE y red vectorial. Puedes utilizar una red completa o una capa que ya contenga únicamente tu cauce principal.
3. En la pestaña 1, selecciona el outlet sobre esa misma red. Para capas propias, utiliza antes **Reanudar / reutilizar resultados existentes** para registrar el MDE y la red.
4. En **Morfometría**, pulsa **Calcular morfometría completa**. El informe inicial se completa con pendientes y cauce principal al terminar la tarea. El botón inferior permite cancelar.
5. En **CHIRPS**, selecciona ClimateSERV v2 y años completos. Vuelve a iniciar la misma consulta para reanudar después de un error o un límite de tiempo; conserva la carpeta de trabajo.

Si persiste el error anterior de `r.to.vect`, primero hace falta obtener/cargar una red vectorial válida. Esta entrega no confirma ni corrige la causa de ese error de conversión, porque no se dispone de su registro de Processing. La pendiente de cuenca sí puede calcularse sin una red; la del cauce se informa como no disponible.

## Validación efectuada

Pruebas ejecutadas con las bibliotecas instaladas de QGIS 3.44.14 y GDAL:

- Red sintética ramificada: selección de ruta de 40 m, exclusión de una rama desconectada y rechazo de outlet alejado.
- MDE sintético plano con pendiente conocida del 10 %: pendiente de cuenca y cauce del 10 %, longitud de 600 m y GeoPackage válido.
- Tarea real de QGIS en segundo plano: finalización, carga de capa, actualización de longitud y recuperación de controles.
- CHIRPS con respuestas simuladas: año bisiesto, reutilización de caché sin HTTP, rechazo de año incompleto y cancelación.
- Escritura del CSV y error de escritura visible al usuario.
- Ocho pestañas con scroll y acceso al final del formulario; geometrías original/proyectada independientes.

No se ejecutó una descarga real completa contra ClimateSERV ni una validación con la cuenca y el MDE del usuario. Las comprobaciones de widgets se ejecutaron en modo offscreen; no constituyen una validación visual en la sesión de escritorio. No se midió una aceleración porcentual.
