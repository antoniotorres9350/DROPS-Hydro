# DROPS-Hydro MX v0.5.0

Esta versión integra recorte y Strahler, selección de carpeta CHIRPS, comparación de distribuciones, CN compuesto, parámetros automáticos del filled DEM, comparación de gastos e inundación SIMWE. Conserva el scroll y el centroide automático de versiones anteriores. No se instala automáticamente ni modifica el ZIP original.

## 1. Red recortada y orden Strahler

Después de delimitar una cuenca se inicia el análisis de terreno y red. Se conserva la red completa para seleccionar otros outlets; se crea una segunda capa **DH_Red_Cuenca_Strahler** recortada al polígono, que pasa a ser la entrada para la morfometría.

Se construye la conectividad hacia el outlet y se asigna Strahler: cabeceras de orden 1; dos afluentes del mismo orden incrementan el orden aguas abajo. Los vértices intermedios se agrupan en tramos entre confluencias, evitando contar cada segmento de celda como una corriente independiente. El GeoPackage también contiene **DH_CaucePrincipal**.

Los tramos desconectados conservan geometría, pero su orden queda vacío y `topology` indica el problema. Si hay ciclos, la red se recorta y se exporta sin asignar órdenes ficticios. Los gastos automáticos se detienen si hay tramos sin orden: primero debe corregirse la topología. El cálculo presupone una red dendrítica y un outlet correcto; no reemplaza una comprobación independiente con direcciones de flujo.

## 2. CHIRPS y distribuciones

La pestaña permite elegir una **carpeta específica para CHIRPS y ajustes**, independiente de la carpeta de trabajo del MDE. Se conserva la descarga anual con caché y reanudación. La carpeta y las rutas de datos se guardan en la sesión.

Para atender el promedio de cuenca solicitado, la opción inicial de ClimateSERV v2 es ahora **promedio espacial de toda la cuenca**. Se promedia espacialmente la precipitación diaria y después se extraen sus máximos anuales. Las coordenadas del centroide siguen calculándose automáticamente y puede seleccionarse la consulta alrededor del centroide como alternativa. El modo v3 directo solo admite esta última opción: no se presenta una muestra puntual como promedio de cuenca.

Se ajustan por máxima verosimilitud:

- Gumbel.
- GEV, con localización libre.
- Lognormal de dos parámetros, con localización fijada en cero.
- Log-Pearson III, ajustada en logaritmos naturales.

Se informa log-verosimilitud, AIC, AICc, BIC y distancia KS. **La distribución recomendada es la de menor AICc entre los ajustes válidos**, aunque puede adoptarse otra desde el menú. Se incluye el jacobiano en Log-Pearson III para que su verosimilitud sea comparable en la escala original. KS se presenta como diagnóstico descriptivo, sin un valor p ingenuo calculado con parámetros ajustados sobre la misma muestra. Se necesitan al menos diez máximos positivos y no constantes; ese mínimo no garantiza precisión para periodos de retorno grandes.

Se guardan automáticamente `comparacion_distribuciones.csv`, `lluvias_diseno.csv` y `maximos_anuales.csv`. También hay un botón para exportarlos a otra carpeta. Los parámetros GEV siguen la convención de SciPy. La conversión subdiaria sigue siendo una aproximación DDF, que debe contrastarse con IDF locales. Referencia: [distribuciones de SciPy](https://docs.scipy.org/doc/scipy/reference/stats.html).

## 3. CN calculado con cobertura y suelo

El CN **no se observa directamente desde un satélite**. Se estima combinando:

1. Cobertura ESA WorldCover 2021 v200, leída remotamente para la extensión de la cuenca, o un raster local compatible.
2. Un raster de grupo hidrológico de suelo: 1=A, 2=B, 3=C y 4=D. Puede utilizarse HYSOGs250m; sus clases duales 11–14 se interpretan como no drenadas, grupo D.
3. Una tabla de equivalencias CN para condición de humedad AMC-II, exportable y editable como CSV con columnas `class,description,A,B,C,D`.

**Debes proporcionar el raster de suelo.** La pestaña incluye un enlace a la fuente HYSOGs; no se inventa el grupo a partir de la cobertura. Referencias: [WorldCover y acceso a los datos](https://esa-worldcover.org/en/data-access), [HYSOGs250m](https://daac.ornl.gov/SOILS/guides/Global_Hydrologic_Soil_Group.html).

El cálculo usa una cuadrícula métrica común, remuestreo por vecino más cercano y máscara de cuenca. El CN medio se pondera por área de celdas válidas. La resolución inicial es 30 m, editable; puede aumentar para no superar aproximadamente cuatro millones de celdas. Esto no mejora la resolución original del suelo. Se exige al menos 95 % de cobertura válida; el porcentaje efectivo se informa. El promedio sobre las celdas válidas es una aproximación al promedio de toda la cuenca.

La tabla base asume bosque, matorral y pastizal en buena condición, cultivos en surcos rectos en buena condición, construido impermeable y humedales saturados. Son hipótesis de escenario que deben revisarse, no una clasificación hidrológica validada por WorldCover. Nieve/hielo y musgos/líquenes no tienen una asignación base. Puede modificarse la tabla antes de calcular. Referencias: [tablas CN de HEC-HMS / TR-55](https://www.hec.usace.army.mil/confluence/hmsdocs/hmstrm/cn-tables) y [CN compuesto por área](https://www.hec.usace.army.mil/confluence/hmsdocs/hmstrm/canopy-surface-infiltration-and-runoff-volume/infiltration/scs-curve-number-loss-model).

Salidas: `cn_cuenca.tif`, `cn_areas.csv` y `cn_crosswalk.csv`. Se reutiliza el resultado durante la sesión cuando no cambian cuenca, rasters, tabla ni resolución.

## 4. Pendientes, Tc y gastos automáticos

Los botones de Gastos/Tc ejecutan la secuencia necesaria: terreno con **filled DEM**, cauce principal, CN y cálculo hidrológico. El filled DEM se toma del paso 3 o de una ruta indicada expresamente. No se reemplaza silenciosamente por un MDE sin rellenar.

- Pendiente media de cuenca: media de pendientes Horn de las celdas dentro de la cuenca.
- Pendiente longitudinal: desnivel del cauce principal dividido por su longitud.
- Altura para Giandotti: elevación media del filled DEM en la cuenca menos elevación del outlet; ya no se usa el rango completo del MDE externo.
- Longitud: ruta principal conectada calculada automáticamente.

Los MDE deben tener elevaciones en metros. Se mantiene el límite de aproximadamente cuatro millones de celdas para terreno; la resolución y cobertura se registran.

Se calculan Kirpich, Giandotti, Témez y NRCS/SCS lag, más el promedio aritmético como alternativa comparativa. No se selecciona automáticamente el menor Tc. Para cada periodo de retorno y cada Tc se calculan:

- **SCS-CN / HU NRCS:** pérdidas con CN medio y pico con el esquema NRCS simplificado.
- **Racional:** intensidad de duración Tc, aproximada desde P24 con DDF. Si Tc supera 24 h, se informa que no se calculó. Requiere revisar su dominio de aplicación y preferiblemente usar IDF locales.
- **HU triangular / coeficiente C:** escurrimiento estimado con un C independiente y editable; no se deriva C del CN.

Los hidrogramas triangulares se ajustan para conservar el volumen de escurrimiento. Se exportan `tiempos_concentracion.csv`, `gastos_metodos.csv` e `hidrogramas.csv`. Este último contiene los tres vértices de cada triángulo, no una serie de lluvia variable ni un hidrograma calibrado. Referencia: [modelo de hidrograma unitario SCS](https://www.hec.usace.army.mil/confluence/hmsdocs/hmstrm/transform/scs-unit-hydrograph-model).

## 5. Inundación: corrección y alcance

Se identificó un fallo concreto en la configuración instalada: el proveedor QGIS de `r.sim.water` requiere `dx` y `dy`, pero el plugin anterior no los proporcionaba. Ahora se preparan MDE, derivadas y máscara sobre una sola cuadrícula métrica. Las derivadas se calculan con un margen alrededor del polígono antes de aplicar la máscara.

Del código Rain2Flood adjunto se aprovecharon los conceptos de comparación de métodos, hidrogramas y exportación. **No se incorporó el radio hidráulico calculado como 0.7 × elevación media**, ni una cota de agua uniforme basada en el mínimo de toda la cuenca: esos valores no representan la geometría hidráulica del cauce.

El modelo implementado es **SIMWE de escurrimiento superficial por lluvia**, con lluvia excedente uniforme obtenida de SCS-CN y Manning editable. La duración se toma de la lluvia de diseño para mantener consistente el volumen de aporte. Se ejecuta en segundo plano y permite cancelar. Se limita la cuadrícula a unas 250 000 celdas, aumentando la resolución si hace falta. No se generan puntos de partículas innecesarios. Un escenario sin lluvia excedente produce directamente raster secos.

Cada escenario se guarda en su propia carpeta e incluye MDE/derivadas, profundidad, descarga superficial, error numérico SIMWE, estilo de profundidad, `processing.log`, `escenario.json` y `resumen_inundacion.csv`. Se comprueban dimensiones, georreferencia y datos válidos. El área mojada del CSV utiliza profundidad > 0.01 m; el volumen superficial es la integral de la profundidad positiva.

**Control numérico añadido:** se compara el almacenamiento superficial con el aporte total de lluvia excedente. Si lo supera en más del 10 %, el resultado se marca **REVISAR / no validado**. Ese umbral es un control de plausibilidad, no una tolerancia calibrada; aprobarlo no reemplaza el balance completo con flujos de salida. En la prueba sintética de seis minutos apareció una superación del aporte, por lo que ese caso quedó marcado para revisión. Los raster se conservan para diagnóstico.

SIMWE no es, por sí solo, una simulación de desbordamiento fluvial con secciones, puentes y condiciones de contorno. El raster de error numérico tampoco es un intervalo de incertidumbre de la inundación. Referencia: [manual GRASS r.sim.water](https://grass.osgeo.org/grass-stable/manuals/r.sim.water.html).

### Mejoras propuestas para la siguiente etapa de inundación

1. Separar explícitamente escenarios de escurrimiento pluvial y desbordamiento fluvial. Para los segundos, incorporar geometría y condiciones hidráulicas, o exportar hidrogramas a un modelo como HEC-RAS 2D.
2. Añadir hietogramas temporales e infiltración y Manning espaciales; comparar eventos observados antes de adoptar escenarios de diseño.
3. Incorporar pruebas de convergencia al variar tamaño de celda y número de partículas, y un balance de masa completo con salida por el borde.
4. Comparar extensión simulada con huellas observadas de inundación y generar escenarios de incertidumbre de CN, lluvia y rugosidad.

## Instalación y flujo recomendado

1. En QGIS, instala `DROPS-Hydro_MX_v0.5.0.zip` desde el administrador de complementos y reinicia QGIS.
2. Calcula el filled DEM y el flujo, genera la red, selecciona el outlet y delimita. Espera el recorte, Strahler y el cauce principal; revisa las advertencias de topología.
3. En CHIRPS, elige carpeta, periodo y modo espacial. Descarga y pulsa **Ajustar y comparar…**. Revisa AICc y la distribución adoptada.
4. En Gastos/Tc, indica el raster de suelo y WorldCover remoto o local. Revisa la tabla CN y el coeficiente C. Pulsa **Calcular todos los gastos y periodos**; los CSV se guardan en la carpeta indicada.
5. En Inundación, elige periodo, Manning, resolución y salida. Revisa el control de volumen, registro y mapa de error además de la profundidad.

## Validación y límites de esta entrega

Pruebas con bibliotecas QGIS 3.44.14, GDAL y SciPy instaladas:

- Red en Y: Strahler 1 + 1 → 2, unión de vértices intermedios y detección de ciclos. La red cíclica se exporta con orden vacío y marca de topología.
- CN sintético de dos áreas iguales: (30 + 77) / 2 = 53.5, con raster y CSV correctos.
- Cuatro distribuciones, cuantiles finitos y jacobiano de Log-Pearson III comprobado.
- Conservación de volumen de los hidrogramas triangulares.
- Secuencia real de tareas QGIS: ajuste → terreno → Strahler → CN → cinco Tc y 45 combinaciones de gasto para tres periodos → CSV.
- Ejecución del código instalado de `grass:r.sim.water`, cargado de forma aislada en el entorno de prueba: caso con lluvia, caso seco, cuadrícula de salida y ejecución en un `QgsTask`. Se generaron mapas y registros. El caso corto reveló la limitación de volumen descrita arriba; no se presenta como validación física.

No se probaron descargas reales de WorldCover/HYSOGs ni una descarga completa CHIRPS para la cuenca del usuario. No se dispone de sus MDE y observaciones para calibrar ni validar resultados hidrológicos o hidráulicos. El antiguo error de conversión de red `r.to.vect` no se ha atribuido a una causa concreta sin su registro; estas mejoras de recorte parten de una red vectorial válida.
